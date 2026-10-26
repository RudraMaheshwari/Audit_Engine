"""Authoritative data dictionary for the GLD transactional dashboards.

Injected as context into the transactional analysis + NL2SQL prompts so the AI interprets the tables
using the business-vetted precomputed columns, instead of re-deriving overrides/discounts from raw
math (which misread free goods, samples, credit memos and SAP technical statuses as risks).

Covers the 7 SAM dashboards plus the additional dashboards registered for other audits (full HCC
analysis, HCC attendee, T&E attendee). Only the audit's active dashboards are queried, but this
dictionary describes every table so any audit interprets them correctly.
"""

TRANSACTIONAL_DATA_DICTIONARY = """\
=== TRANSACTIONAL DATA DICTIONARY (authoritative — use these exact columns; the tables carry
business-vetted precomputed flags, so PREFER them over re-deriving metrics from raw math. Ignore
columns that are entirely null/0 for the scope you are testing) ===

AUDIT POPULATION KEY (applies to EVERY table below): ENGAGEMENT_NUMBER (INT) identifies the audit a row
belongs to. These tables hold MANY audits' rows (different countries and audits). EVERY query MUST first
filter ENGAGEMENT_NUMBER to this audit's engagement number(s) — the "AUDIT POPULATION FILTER" predicate
supplied at runtime (of the form ENGAGEMENT_NUMBER IN (<the audit's engagement numbers>)). This is the PRIMARY, mandatory scoping
mechanism for ALL dashboards. Compare it unquoted (it is an integer); never try_cast it. Without it, a
metric mixes other audits' data — for example another territory's hospitals, universities, or
distributors appearing in results that should be limited to the audit's territory.

UNFED / DEAD-JOIN COLUMNS (confirmed empty/unusable in this source — NEVER build a finding on their
emptiness; a blank, "all null", or 0-match result here is a DATA-FEED gap, not a control failure):
- AP disbursement/payment ACTIVITY_ID is 100% NULL. Do NOT reconcile HCC events to AP payments by
  ACTIVITY_ID — the join returns zero matches by construction. A "no matching payment", "events without
  payment", or "unmatched approvals" result is a data-model limitation, NOT missing payments — do not raise it.
- AP disbursement/payment APPROVAL_DATE is 100% NULL. NEVER conclude "missing approval", "payment before
  approval", or an "approval-to-payment sequence issue" for AP records from it.
- HCC GO classification (CATEGORY_OF_HCP_BY_GO_NON_GO_OR_CROSS_BORDER) is populated on under 1% of rows
  and only ever holds "GO/HCP of Influence" — there is NO "Non-GO" value. A blank does NOT mean "not a
  Government Official" or "not assessed". NEVER compute GO-coverage % or flag "GO identification deficient
  / X% not flagged as GO" from it.
- HCC APPROVAL_DATE is populated on only ~3% of rows — do not treat blanks as missing approvals.

STATUTORY / TAX / REGULATOR PAYMENTS ARE NOT THIRD-PARTY FCPA RISK (exclude them from bribery /
one-time-vendor / cash-advance / donation / sponsorship / GO-payment / procurement findings; at most a
books-and-records note if a genuine miscoding is actually computed):
- Payments booked to TAX / LIABILITY / CLEARING GL accounts are statutory/accounting entries, not
  third-party payments: GL_ACCOUNT_CODE beginning '21' (e.g. VAT Payable, Input/Output Tax, Income Tax,
  Payroll Tax, Fringe Benefit Tax, Withholding, GR/IR Clearing, Misc A/P) and equity-tax accounts
  beginning '3664'. Do NOT flag these as improper payments.
- Payments to TAX AUTHORITIES / REGULATORS / GOVERNMENT FUNDING BODIES are legally required and are NOT
  bribery risk. GOVERNMENT_VENDORS does not distinguish them (they appear as 'Other') — identify them by
  payee name, e.g. Australian Taxation Office, Inland Revenue, Office of State Revenue / State Revenue
  Office, Therapeutic Goods Administration, Department of Health & Ageing, Pharmaceutical Management
  Agency (PHARMAC), Customs, Superannuation. Never score these as sponsorships, donations, cash advances,
  one-time-vendor, procurement, or GO-payment risks.
- ADVANCE_PAYMENT = 'Advance' denotes a VENDOR down/advance payment (a normal commercial mechanism); it
  is NOT an employee cash advance or petty cash. Do NOT label vendor advance payments, or any statutory
  payment above, as a "cash advance".

SALES / PRICING / REVENUE / OTC dashboards (pricing_dashboard, revenue_dashboard, otc_dashboard):
- PRICE OVERRIDE — use TRUE_PRICE_OVERRIDE only ('Y' = a genuine manual price override; 'N' = none).
  DO NOT treat BILLING_MANUAL_PRICE_CHANGE_STATUS ("A") or ORDER_MANUAL_PRICE_CHANGE_STATUS
  ("Manual price change") as an override — these are SAP technical statuses present on almost every
  row and DO NOT mean an override occurred. Override rate = COUNT(TRUE_PRICE_OVERRIDE='Y') over
  genuine SALE rows — never "all rows".
- DISCOUNT — use the precomputed ACTUAL_DISCOUNT_PERCENTAGE and ACTUAL_DISCOUNT_AMOUNT(_DC). DO NOT
  derive discount from (list − net); that yields false 100%+ discounts for free goods and credit memos.
- TRANSACTION_TYPE — 'Sale' = genuine sale; 'Credit Memo' = credit note / adjustment / return;
  'Product Return' / 'Debit Memo' / 'Free Good' are their own types. Compute discount%/override% ONLY
  over TRANSACTION_TYPE='Sale'. EXCLUDE 'Credit Memo'.
- CREDIT-NOTE VALUE IS POPULATED: for TRANSACTION_TYPE='Credit Memo', the net value column
  (e.g. SALE_NET_VALUE_DC) carries the credit amount and is fully populated. Compute credit-note metrics
  by VALUE and count. Do NOT report credit value as 0/blank — a zero total means the WRONG column was
  summed (or the type filter was off), NOT that values are missing.
- CREDIT NOTES / RETURNS (legitimately net zero/negative — never a discount or override risk on their
  own): TRANSACTION_TYPE='Credit Memo'; RETURN_ITEM='X'; item category 'G2N' in
  BILLING_DOCUMENT_ITEM_CATEGORY / SALES_DOCUMENT_ITEM_CATEGORY; SALE_TYPE_DESCRIPTION 'CR for
  Adjustment'; ORDER_DOCUMENT_TYPE_DESCRIPTION 'Credit memo request'.
- FREE GOODS / SAMPLES (zero price is EXPECTED — never a discount or override): FREE_GOOD_TYPE
  populated, SUM_TOTAL_FREE_GOODS, or free-good order/sale types. Also exclude from discount/override.
  Not every zero-price / zero-INVOICE_SALE_PRICE row is a genuine free good — some are rebates, returns,
  clinical-trial stock, etc. So when reporting a free-goods finding, phrase it as "Potential Free Goods"
  (not confirmed free goods) and, in the risk_description, CITE the ORDER_REASON_DESCRIPTION column —
  include a few sample reason values actually present in the rows (e.g. 'Rebate', 'Clinical Trial Stock',
  'Government Rebate') so the reviewer can see the mix of reasons behind the zero-price population.
- REASON-OF-TRANSACTION FIELDS — to determine WHY a transaction occurred (and thus whether a zero-price
  / discounted / sampled row is a genuine risk or a normal mechanism), use these three columns together,
  in priority order: (a) ORDER_REASON_DESCRIPTION (PRIMARY — the commercial reason, e.g. 'Government
  Rebate', 'AN Price Adjustment', 'Clinical Trial Stock', 'EDI', 'Rebate'); (b) ORDER_DOCUMENT_TYPE_DESCRIPTION
  (e.g. 'Credit memo request'); (c) SALE_TYPE_DESCRIPTION (e.g. 'CR for Adjustment'). Cite the actual
  values present in the rows when describing a finding. A government rebate, price adjustment, or
  credit/return reason is a normal mechanism, not per se a risk — do not score it as one without a
  separate red flag.
- Value columns: NET_SALES(_DC), SALE_NET_VALUE(_DC), BILLING_NET_VALUE(_DC), *_LIST_VALUE(_DC),
  SALE_PRICE_MARGIN_PERCENTAGE, MARGIN. Amounts are often STRING — wrap in try_cast(... AS DOUBLE).

AP PAYMENTS dashboards (disbursement_dashboard, payment_dashboard):
- GL MISCODING is PRECOMPUTED — use HCC_ACCOUNTS ('HCC Accounts' vs 'Non HCC Accounts'),
  AUDIT_AREAS_CATEGORIZED_BY_GL_ACCOUNTS, POTENTIAL_MIS_CATEGORIZATION_TO_NON_HCC_ACCOUNTS
  ('Potential Mis-categorization') and AUDIT_AREA_MIS_CATEGORIZATION_TO_NON_HCC_ACCOUNTS rather than
  re-deriving GL logic. A specific GL code is "the correct/mandatory GL" ONLY when the authoritative
  HCC GL Account Map designates it so — do NOT assert that a payment belongs to a particular GL (e.g. a
  Market-Research GL) unless that GL and activity appear in the map. If the expected GL is not found in
  the map, describe it as a coding INCONSISTENCY requiring confirmation, not a confirmed wrong-GL breach.
- CREDIT MEMOS: DOC_TYPE='KG' / DOC_DESCRIPTION 'Vendor credit memo' (net negative — expected).
- CLINICAL-TRIAL CLASSIFICATION — the TRIAL column being non-null is NOT a reliable clinical-trial
  classifier: on these tables a non-null TRIAL value covers almost the entire AP population (e.g. whole
  company codes), so "TRIAL IS NOT NULL" will sweep in nearly all disbursements. NEVER size clinical-trial
  spend or conclude trial mis-categorisation from that flag alone. First validate the population is
  genuinely clinical-trial (e.g. trial IDs/protocol references, trial cost centres, GL/activity mapping,
  or vendor nature) and state the total as an analytical population pending validation (Further Review
  Required) — do NOT present it as confirmed clinical-trial spend until the classification is proven.
- Amounts: DOCUMENT_CURRENCY_INVOICE_AMT, HOME_CURRENCY_INVOICE_AMT, DOCUMENT_CURRENCY_INVOICE_AMT_USD.
- Vendor: SUPPLIER_NAME / VENDOR_NAME. Anomaly flags precomputed (DUP_PAYMENT_FLAG, ROUND_DOLLAR_FLAG,
  HIGH_DOLLARS_PAYMENTS_FLAG, HIGH_RISK_VENDORS_FLAG) — use them.
- GOVERNMENT-OWNED ENTITY (GOE) IDENTIFICATION — use GOVT_FLAG as the AUTHORITATIVE government flag:
  GOVT_FLAG = 'Government Vendors' means a government-owned/controlled payee; GOVT_FLAG =
  'Non Government Vendors' means NON-government. It derives from GOVERNMENT_VENDORS:
  GOVERNMENT_VENDORS = 'Other' → NON-government (GOVT_FLAG = 'Non Government Vendors'); every OTHER
  GOVERNMENT_VENDORS value (Hospital, University, Clinic, Institute, Associations, Doctors, Center, …)
  → government (GOVT_FLAG = 'Government Vendors'). CRITICAL: 'Other' is NOT a government category — it
  is the non-government bucket.
  * For the PROCUREMENT_FROM_GOVERNMENT_OWNED_ENTITIES scope, restrict the population to
    GOVT_FLAG = 'Government Vendors' (equivalently GOVERNMENT_VENDORS <> 'Other'). NEVER build the GOE
    finding on GOVERNMENT_VENDORS = 'Other' rows — they are non-government and out of this scope.
  * Identify the reason / nature of a GOE payment from INVOICE_DESCRIPTION and PO_DESCRIPTION (this
    scope targets genuine purchases from government-owned businesses).
  * A blocked/inactive vendor-master status (BLOCKED_UNBLOCKED_FLAG / ACTIVE_IN_ACTIVE_FLAG) or a
    period-end clustering of payments is, on its own, Further Review Required — the master status is
    as-of extraction (not necessarily the payment date) and period-end timing has benign explanations.
    Do NOT score these as a High Risk without corroborating evidence (e.g. computed anomaly flags).
  * ONE_TIME_VENDOR POPULATION (apply for the ONE_TIME_VENDOR scope — define the population BEFORE quoting
    any count or total):
    - The population is ONLY payments processed through a one-time / generic vendor account — the genuine
      signal is a BLANK / generic vendor master (blank VENDOR_ID and/or VENDOR_NAME). RESTRICT the WHERE
      clause to that signal. Do NOT build the finding on, or quote a total for, the broader AP population
      (all disbursements) — a count/total taken over the whole ledger is INVALID for this scope. State the
      restricted population the figure is based on (e.g. "N payments / USD X processed through one-time /
      generic vendor accounts"), never the full-AP figure.
    - EXCLUDE from this population (enforce these as WHERE predicates in the SQL, not merely in prose):
      (a) statutory / tax / regulator payees (per the statutory rule above);
      (b) INTERCOMPANY / AFFILIATE payees — any underlying payee (SUPPLIER_NAME) that is a J&J group entity,
          e.g. a name containing 'JOHNSON & JOHNSON', 'J&J', 'JNJ', 'WORLD HEADQUARTERS', or an affiliate /
          international-HQ marker. These are intragroup transfers, NOT external third-party HCC exposure —
          NEVER fold their counts/USD into the one-time-vendor HCC figure; if mentioned at all, report them
          SEPARATELY as intercompany; and
      (c) logistics carriers and other real, named standard vendors whose master is clearly identifiable.
      A count/total that still includes J&J intercompany or statutory payees is INVALID for this scope.
    - RECIPIENT CLASSIFICATION must come from the data, not assumption: SELECT GOVT_FLAG /
      GOVERNMENT_VENDORS (government vs non-government, per the GOE rule above) and read HCP / HCO nature
      from the payee name / INVOICE_DESCRIPTION / PO_DESCRIPTION. Only state a recipient is an HCP, HCO, or
      government entity when the data supports it; otherwise say the recipient could not be classified from
      the data. IMPORTANT: GOVERNMENT_VENDORS='Other' (→ GOVT_FLAG 'Non Government Vendors') is only the
      ERP's own bucket and is NOT a clean bill of health — when the underlying payee NAME denotes a
      HOSPITAL, UNIVERSITY, CLINIC, MEDICAL CENTER, or a health / patient ASSOCIATION (in English OR the
      local language / script — e.g. 병원, 의료원, 대학교, 대학병원, 협회), flag it as a HEALTHCARE /
      potential HCO- or government-linked recipient warranting classification review; do NOT report such a
      payee as simply 'Non-Government / Not HCP-linked'.
    - WORDING (keep this a SCORED finding — do NOT downgrade): repeated use of a one-time / generic vendor
      account for the same underlying recipient is a valid risk indicator even when individual payments may
      be legitimate. Frame it as indicating the recipient should be brought through standard vendor
      onboarding / approval, and describe any onboarding- or approval-bypass as a risk for auditors to
      investigate — NOT as a confirmed violation of every payment.

HCC dashboard (hcc_dashboard — FFS / HCC events):
- Event/FFS data: HCP_NAME, EVENT_NAME, TOTAL_ACTIVITY_APPROVED_AMOUNT, FAIR_MARKET_VALUE, FMV_FLAG,
  APPROVED_AMOUNT_SAME_AS_PAID_AMOUNT_FLAG, GOA_FLAG, HCC_ACCOUNTS. Judge FFS/FMV/approval risk here —
  NOT pricing/override/discount.
- ACTIVITY TYPE lives in the CATEGORY column, whose values name the activity (e.g. 'Fee for Service',
  'Event Invitee', 'Advisory Board', 'Speaker'). To scope a query to an activity, match CATEGORY
  against the REAL distinct values provided in the data summary — never a guessed term. A congress /
  organized-event filter must include the 'Event Invitee' / 'Organized' style values, not just the
  literal word 'Congress'.
- EVENT-SPEND AMOUNT: use DOCUMENT_CURRENCY_INVOICE_AMT_USD; when it is NULL or 0, fall back to
  ACTIVITY_APPROVED_AMT_USD. Compute spend as
  COALESCE(NULLIF(try_cast(DOCUMENT_CURRENCY_INVOICE_AMT_USD AS DOUBLE), 0), try_cast(ACTIVITY_APPROVED_AMT_USD AS DOUBLE)).
- DORMANT / UNFED COLUMNS — DO NOT infer "no HCC monitoring / no reviews / no validations" from these:
  VALIDATION_RESULT and VALIDATION_DATE are 100% NULL across the ENTIRE dataset (all tables), and
  HCCO_REVIEW_DATE / EVENT_REVIEWER_WWID are populated on well under 1% of rows. These columns are
  simply not fed in this source — their emptiness is a DATA-FEED gap, NOT evidence that the company
  failed to perform HCC monitoring or validation. A COUNT of these columns returning 0 (or ~0) is
  MEANINGLESS. HCC monitoring/validation execution is evidenced in DOCUMENTS (GACs, MAPs, monitoring
  workpapers), which are NOT in these transactional tables. Therefore: do NOT compute a
  "reviewed_count / validated_count" metric from these columns, and NEVER raise an HCC_MONITORING
  "zero reviews/validations / no monitoring performed" finding from them.

TRAVEL & EXPENSE dashboard (travel_and_expense_dashboard):
- Employee T&E: EXPENSE_NAME, RPT_ENT_EXP_TYPE_NM, LC_INVC_LINE_AMT, MERCH_CAT_CD, plus precomputed
  anomaly flags (DUP_PAYMENT_FLAG, ROUND_DOLLAR_FLAG, SUSPICIOUS_MCC_FLAG). This is expense data —
  NOT pricing/override/discount.

EQUIPMENT PLACEMENT:
- There is NO equipment-placement dataset in these tables (the EQUIPMENTS / EQUIPMENT_SCORE columns
  are keyword flags, not placement records). DO NOT infer or report an equipment-placement finding
  from these transactional tables.

HCC full analysis dashboard (hcc_full — the richer HCC/FFS activity table; separate from the
user-input hcc_dashboard above):
- This table carries business-vetted PRECOMPUTED flags — PREFER them over re-deriving from raw math.
  Judge HCC/FFS risk from the flags, not pricing/override/discount logic.
- FMV: FMV_FLAG, FAIR_MARKET_VALUE(_USD), PERCENTAGE_DEVIATION_OF_APPROVED_AMOUNT_FROM_FMV,
  BUCKETING_OF_PERCENTAGE_DEVIATION. Approval integrity: GOA_FLAG, APPROVAL_BEFORE_START_DATE,
  APPROVAL_TO_PAYMENT_DATE_ORDER_FLAG, INTERRUPTION_IN_CHRONOLOGICAL_ORDER,
  APPROVED_AMOUNT_SAME_AS_PAID_AMOUNT_FLAG, DIFF_BETWEEN_PAID_AND_APPROVED_AMOUNT.
- Amounts (often STRING — wrap in try_cast(... AS DOUBLE)): TOTAL_ACTIVITY_APPROVED_AMOUNT,
  ACTIVITY_APPROVED_AMT_USD, AMOUNT_REPORTED(_IN_USD). Dollar anomalies: HIGH_DOLLARS_PAYMENTS_FLAG,
  ROUND_FIGURES_FLAGS. Duplicates: DUP_PAYMENT_FLAG, DUP_INV_PAYMENT_FLAG, DUPLICATE_INV_IDENTIFIER.
- GL / categorisation: HCC_ACCOUNTS, HCC_ACTIVITY_GL_ACCOUNT, GL_ACCOUNT_VALIDATION_FLAG,
  AUDIT_AREA_MIS_CATEGORIZATION_TO_NON_HCC_ACCOUNTS. Transparency reporting: TRANSPARENCY_REPORTING_REQUIRED,
  AMOUNT_REQUIRED_TO_BE_REPORTED_FLAG, PAYMENTS_MADE_VS_PAYMENT_REPORTED_FLAG, THRESHOLD_VALUE_FOR_REPORTING.
- HCP/GO: HCP_NAME, HCP_CLASSIFICATION, HCP_ID, CATEGORY_OF_HCP_BY_GO_NON_GO_OR_CROSS_BORDER,
  NATURAL_PERSON. Event: EVENT_NAME, EVENT_START_DATE/END_DATE, LOCATION_VENUE, INAPPROPRIATE_VENUES_FLAG,
  FUTURE_EVENT_FLAG, EVENT_BETWEEN_AUDIT_PERIOD_FLAG, MANDATORY_FIELD_EMPTY_FLAG, MIN_MAX_ATTENDEE_FLAG.
- Bank/payee integrity: PAID_TO_CORRECT_BANK_FLAG, PAID_TO_CORRECT_VENDOR_ID_FLAG,
  VENDOR_AND_BANK_COUNTRY_MATCH_FLAG, PAID_TO_RED_FLAG_COUNTRY_BANK, PAID_TO_CORRECT_ACCOUNT_FLAG.
- The SAME dormant-column caveat applies: VALIDATION_RESULT/VALIDATION_DATE (and HCCO_REVIEW_DATE /
  reviewer WWID on most rows) are unfed — their emptiness is a data-feed gap, NOT proof of missing
  monitoring/validation. Never raise a "zero reviews/validations" finding from them.
- ACTIVITY_ID is the event/activity key; join to hcc_attendee on ACTIVITY_ID for attendee-level tests.

HCC attendee dashboard (hcc_attendee — child of hcc_full, one row per attendee per event):
- Join to hcc_full by ACTIVITY_ID. Attendee-level fields: ATTENDEE_NAME, ATTENDEE_ID,
  ATTENDEE_DATA_FLAGS (attendee-data availability status), NO_OF_ATTENDEES, MIN_ATTENDEES,
  MAX_ATTENDEES, MIN/MAX_EVENT_COST_PER_ATTENDEE. Repeat-attendance signals: COUNT_TIMES_ATTENDED,
  DIFF_DAYS_ATTENDED_ANOTHER_EVENT, AVG_DAYS_VISIT_AGAIN. Use for attendee-count thresholds, cost per
  attendee, and repeat-HCP attendance — NOT pricing/override/discount.

T&E attendee dashboard (travel_expense_attendee — child of travel_and_expense, one row per attendee):
- Join to travel_and_expense by EVENTID. Fields: ATTENDEE_NAME, ATTENDEE_TYPE ('Employee' / 'HCP'),
  HCP_INDICATOR, ATNDEE_EMP_IND, ATTENDEE_COMPANY_NAME. Use for attendee composition (HCP vs employee)
  and HCP presence at T&E events — NOT pricing/override/discount.

NOTE: hcc_full, hcc_attendee and travel_expense_attendee are shared across audits (they hold multiple
audits' rows). As with every table, isolate the current audit's data via ENGAGEMENT_NUMBER (the AUDIT
POPULATION FILTER) FIRST; COMPANY / COUNTRY / MRC / cost centre may be used as an ADDITIONAL narrowing,
never as a substitute for the engagement filter.

PRESCRIBED ANALYSES (authoritative — run these EXACTLY with the fields named here. These columns are
confirmed present; some are USD fields and counterparty/reason columns NOT listed in the per-table
sections above. Always apply the ENGAGEMENT_NUMBER audit-population filter first):

1. PAYMENTS TO TPI (payment_dashboard + the TPI Inventory List):
   - Take each third-party name from the TPI INVENTORY LIST (injected separately as the "TPI INVENTORY"
     block) and match it against SUPPLIER_NAME in the payments table, case-insensitively
     (UPPER(SUPPLIER_NAME) LIKE '%<WORD>%').
   - The amount paid to a TPI is DOCUMENT_CURRENCY_INVOICE_AMT_USD.
   - Report (a) the TOTAL USD paid to TPIs, and (b) that total as a PERCENTAGE of total payments in the
     table — both over the engagement-filtered population.

2. CREDIT NOTES (revenue_dashboard):
   - TRANSACTION_TYPE = 'Credit Memo'. Counterparty is CUSTOMER_NAME.
   - The USD credit-note amount is TOTAL_CREDIT_NOTES (this is the USD field; the *_DC net-value columns
     named above are the document-currency equivalent). Report by CUSTOMER_NAME and in total.

3. SALES AMOUNT (revenue_dashboard):
   - TRANSACTION_TYPE = 'Sale'. Counterparty is CUSTOMER_NAME.
   - The USD sale amount is SALES_AMOUNT (USD field; the *_DC columns above are document-currency).
     Report by CUSTOMER_NAME and in total.

4. DISCOUNTS (revenue_dashboard):
   - USD discount amount is ACTUAL_DISCOUNT_AMOUNT; discount rate is ACTUAL_DISCOUNT_PERCENTAGE (both
     precomputed — never derive from list−net). Counterparty is CUSTOMER_NAME.
   - Compute over TRANSACTION_TYPE = 'Sale' only (exclude 'Credit Memo').

5. USE OF VALID GL ACCOUNTS (payment_dashboard):
   - The account is GL_ACCOUNT_CODE. The REASON for a payment is carried across INVOICE_DESCRIPTION,
     DOC_DESCRIPTION, PO_DESCRIPTION and GL_ACCOUNT_DESCRIPTION — read them together.
   - IMPORTANT: treat GL_ACCOUNT_DESCRIPTION as an authoritative reason ONLY for valid HCC-related GL
     accounts that appear in the GL Account mapping document; for non-HCC accounts it is not authoritative.
   - GL miscoding itself stays PRECOMPUTED (use HCC_ACCOUNTS / POTENTIAL_MIS_CATEGORIZATION… above) — do
     not re-derive GL correctness by hand.

6. ACTIVITY SPEND — Congress / FFS / Charitable Contributions / etc. (hcc_dashboard):
   - Identify the activity from CATEGORY (match the REAL distinct values present, never a guessed word).
   - USD amount spent is DOCUMENT_CURRENCY_INVOICE_AMT_USD.
   - HCP GO / non-GO classification is CATEGORY_OF_HCP_BY_GO_NON_GO_OR_CROSS_BORDER — recall the caveat
     above: this column is sparsely populated and only ever holds a GO value, so a blank is NOT "Non-GO"
     and you must NOT compute a GO-coverage percentage from it.
"""
