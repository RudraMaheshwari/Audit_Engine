from pathlib import Path
from typing import Optional

import openpyxl

from src.models.schemas import DocumentChunk, DocumentSource
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Sheet name (lowercase, stripped) → FCPA scope_id
_SHEET_TO_SCOPE_ID: dict[str, str] = {
    "due diligence":                                          "DUE_DILIGENCE",
    "congresses events":                                      "CONGRESSES__JJ_ORGANIZED_EVENTS",
    "congresses & jj organized events":                       "CONGRESSES__JJ_ORGANIZED_EVENTS",
    "fee for service":                                        "FEE_FOR_SERVICE__DOCTOR_CONSULTING",
    "fee for service (doctor consulting fees)":               "FEE_FOR_SERVICE__DOCTOR_CONSULTING",
    "sponsorships":                                           "SPONSORSHIPS",
    "tenders":                                                "TENDERS",
    "value add and psp":                                      "VALUE_ADD__PATIENT_SUPPORT_PROG",
    "value add & patient support programs":                   "VALUE_ADD__PATIENT_SUPPORT_PROG",
    "educational grants":                                     "EDUCATIONAL_GRANTS",
    "equipment placement":                                    "EQUIPMENT_PLACEMENT",
    "product samples":                                        "PRODUCT_SAMPLES",
    "donations":                                              "DONATIONS",
    "expense reports":                                        "EXPENSE_REPORTS",
    "free goods":                                             "FREE_GOODS",
    "pricing, discounts, & margins":                          "PRICING__DISCOUNTS__MARGINS__DISTRIBUTORS",
    "pricing discounts margins":                              "PRICING__DISCOUNTS__MARGINS__DISTRIBUTORS",
    "credit notes":                                           "CREDIT_NOTES",
    "product training":                                       "PRODUCT_TRAINING",
    "research grants":                                        "RESEARCH_GRANTS__INVESTIGATOR_INITIATED_STUDIES",
    "research grants / investigator initiated studies (iis)": "RESEARCH_GRANTS__INVESTIGATOR_INITIATED_STUDIES",
    "payments in ap to distributors":                         "PAYMENTS_IN_AP_TO_DISTRIBUTORS",
    "rebates paid to distributors":                           "REBATES_PAID_TO_DISTRIBUTORS",
    "price overrides":                                        "PRICE_OVERRIDES",
    "contract sales agents":                                  "CONTRACT_SALES_AGENTS",
    "contract manufacturing vendors":                         "CONTRACT_MANUFACTURING_VENDORS",
    "fcpa training":                                          "FCPA_TRAINING",
    "former government employees":                            "FORMER_GOVERNMENT_EMPLOYEES",
    "government affairs":                                     "GOVERNMENT_AFFAIRS",
    "hcc environment":                                        "HCC_ENVIRONMENT",
    "hcc monitoring":                                         "HCC_MONITORING",
    "interaction with product and site regulators":           "INTERACTION_WITH_PRODUCT_AND_SITE_REGULATORS",
    "joint ventures co promotion":                            "JOINT_VENTURES_CO_PROMOTION",
    "joint ventures / co-promotion":                          "JOINT_VENTURES_CO_PROMOTION",
    "legal fees":                                             "LEGAL_FEES",
    "lobbyists":                                              "LOBBYISTS",
    "local travel agencies":                                  "LOCAL_TRAVEL_AGENCIES",
    "logistics intermediaries customs":                       "LOGISTICS_INTERMEDIARIES__CUSTOMS",
    "logistics intermediaries / customs":                     "LOGISTICS_INTERMEDIARIES__CUSTOMS",
    "marketing research projects":                            "MARKETING_RESEARCH_PROJECTS",
    "media and public relations vendors":                     "MEDIA_AND_PUBLIC_RELATIONS_VENDORS",
    "one time vendor":                                        "ONE_TIME_VENDOR",
    "payments to government agencies":                        "PAYMENTS_TO_GOVERNMENT_AGENCIES",
    "payments to government officials":                       "PAYMENTS_TO_GOVERNMENT_OFFICIALS",
    "process to identify government officials":               "PROCESS_TO_IDENTIFY_GOVERNMENT_OFFICIALS",
    "procurement from government owned entities":             "PROCUREMENT_FROM_GOVERNMENT_OWNED_ENTITIES",
    "product promotion via third party":                      "PRODUCT_PROMOTION_VIA_THIRD_PARTY",
    "royalties paid to public hcps":                          "ROYALTIES_PAID_TO_PUBLIC_HCPS",
    "training for policymakers":                              "TRAINING_FOR_POLICYMAKERS",
    "market access":                                          "MARKET_ACCESS",
    "patient advocacy":                                       "PATIENT_ADVOCACY",
    "strategic affairs":                                      "STRATEGIC_AFFAIRS",
    "clinical trials":                                        "CLINICAL_TRIALS",
    "audit fees":                                             "AUDIT_FEES",
    "cash advances, petty cash, manual checks":               "CASH_ADVANCES__PETTY_CASH__MANUAL_CHECKS",
    "cash advances petty cash manual checks":                 "CASH_ADVANCES__PETTY_CASH__MANUAL_CHECKS",
    # Abbreviated aliases used by the actual CPR Excel sheet tabs
    "pricing, discounts & margins":                           "PRICING__DISCOUNTS__MARGINS__DISTRIBUTORS",
    "pricing discounts & margins":                            "PRICING__DISCOUNTS__MARGINS__DISTRIBUTORS",
    "ap to distributors nonreb":                              "PAYMENTS_IN_AP_TO_DISTRIBUTORS",
    "rebates via inv discount":                               "REBATES_PAID_TO_DISTRIBUTORS",
    "cash adv petty cash manual":                             "CASH_ADVANCES__PETTY_CASH__MANUAL_CHECKS",
    "former gov employees":                                   "FORMER_GOVERNMENT_EMPLOYEES",
    "product site regulators":                                "INTERACTION_WITH_PRODUCT_AND_SITE_REGULATORS",
    "jv co-promotion":                                        "JOINT_VENTURES_CO_PROMOTION",
    "customs brokers":                                        "LOGISTICS_INTERMEDIARIES__CUSTOMS",
    "media pr vendors":                                       "MEDIA_AND_PUBLIC_RELATIONS_VENDORS",
    "payments to govt agencies":                              "PAYMENTS_TO_GOVERNMENT_AGENCIES",
    "payments to govt officials":                             "PAYMENTS_TO_GOVERNMENT_OFFICIALS",
    "gov official identification":                            "PROCESS_TO_IDENTIFY_GOVERNMENT_OFFICIALS",
    "govt-owned procurement":                                 "PROCUREMENT_FROM_GOVERNMENT_OWNED_ENTITIES",
    "3rd party promotion":                                    "PRODUCT_PROMOTION_VIA_THIRD_PARTY",
    "royalties to public hcps":                               "ROYALTIES_PAID_TO_PUBLIC_HCPS",
    "training policymakers":                                  "TRAINING_FOR_POLICYMAKERS",
}

# 1-based column indices in each scope area sheet
_COL_SCOPE_DESCRIPTION     = 3   # C — what this scope area covers
_COL_KEY_RED_FLAGS         = 4   # D — risk indicators / red flags
_COL_APPLICABLE_POLICIES   = 5   # E — relevant policy documents (besides HCBI)
_COL_AUDIT_STEPS           = 6   # F — IDR / questionnaire review steps
_COL_DATA_NAME             = 7   # G — name of the data / data set
_COL_DATA_SOURCE           = 8   # H — source of the data
_COL_DATA_CALCULATIONS     = 9   # I — data calculations / analysis steps
_COL_SAMPLING_RATIONALE    = 10  # J — rationale for sampling approach
_COL_EXPECTED_SAMPLE_SIZE  = 11  # K — expected sample size
_COL_COMMON_OBSERVATIONS   = 15  # O — typical fieldwork observations
_COL_ADDITIONAL_GUIDELINES = 16  # P — supplementary guidelines / comments

# First 3 rows are section group headers, column headers, and descriptions
_HEADER_ROWS = 3

_DOCUMENT_NAME = "CPR AI - System Guide.xlsx"


def _read_column_text(ws, col_idx: int, start_row: int) -> str:
    """Collect non-empty cell values from a column starting at start_row, joined with newline."""
    parts: list[str] = []
    for row in ws.iter_rows(
        min_row=start_row,
        min_col=col_idx,
        max_col=col_idx,
        values_only=True,
    ):
        val = row[0]
        if val is not None:
            text = str(val).strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


def _estimate_tokens(text: str) -> int:
    return max(1, int(len(text.split()) * 1.4))


def _resolve_scope_id(sheet_name: str) -> Optional[str]:
    key = sheet_name.lower().strip()
    if key in _SHEET_TO_SCOPE_ID:
        return _SHEET_TO_SCOPE_ID[key]
    # Partial match fallback — handles minor wording differences in actual file
    for k, v in _SHEET_TO_SCOPE_ID.items():
        if k in key or key in k:
            return v
    return None


class CPRScopeExtractor:
    """
    Reads CPR AI - System Guide.xlsx and produces two DocumentChunks
    per scope area sheet:
      - Chunk 1 (risk_profile):   cols C + D + E + P  (scope desc, red flags, policies, guidelines)
      - Chunk 2 (audit_guidance): cols F + O           (IDR review steps, common observations)

    Each chunk is tagged with the FCPA scope area ID as scope_id so that
    VectorIndexManager.similarity_search can filter by the identified scope area.
    Skips the "Index" sheet and any sheet whose name cannot be mapped to a scope_id.

    Three chunks per sheet:
      - Chunk 1 (risk_profile):      cols C + D + E + P
      - Chunk 2 (audit_guidance):    cols F + O
      - Chunk 3 (sampling_guidance): cols I + J + K
    """

    def extract(self, file_path: str | Path) -> list[DocumentChunk]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"CPR scope file not found: {path}")

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        chunks: list[DocumentChunk] = []
        skipped: list[str] = []
        start_row = _HEADER_ROWS + 1  # row 4

        for sheet_name in wb.sheetnames:
            if sheet_name.strip().lower() == "index":
                continue

            scope_id = _resolve_scope_id(sheet_name)
            if scope_id is None:
                logger.warning(
                    "[CPRScopeExtractor] No scope_id mapping for sheet '%s' — skipping", sheet_name
                )
                skipped.append(sheet_name)
                continue

            ws = wb[sheet_name]

            scope_desc    = _read_column_text(ws, _COL_SCOPE_DESCRIPTION,     start_row)
            red_flags     = _read_column_text(ws, _COL_KEY_RED_FLAGS,          start_row)
            policies      = _read_column_text(ws, _COL_APPLICABLE_POLICIES,    start_row)
            audit_steps   = _read_column_text(ws, _COL_AUDIT_STEPS,            start_row)
            data_name     = _read_column_text(ws, _COL_DATA_NAME,              start_row)
            data_source   = _read_column_text(ws, _COL_DATA_SOURCE,            start_row)
            data_calcs    = _read_column_text(ws, _COL_DATA_CALCULATIONS,      start_row)
            sampling_rat  = _read_column_text(ws, _COL_SAMPLING_RATIONALE,     start_row)
            expected_size = _read_column_text(ws, _COL_EXPECTED_SAMPLE_SIZE,   start_row)
            observations  = _read_column_text(ws, _COL_COMMON_OBSERVATIONS,    start_row)
            guidelines    = _read_column_text(ws, _COL_ADDITIONAL_GUIDELINES,  start_row)

            # Chunk 1 — Risk Profile (C + D + E + F + G + H + P)
            risk_parts: list[str] = []
            if scope_desc:
                risk_parts.append(f"Scope Description:\n{scope_desc}")
            if red_flags:
                risk_parts.append(f"Key Red Flags:\n{red_flags}")
            if policies:
                risk_parts.append(f"Applicable Policies (apart from HCBI Guide):\n{policies}")
            if audit_steps:
                risk_parts.append(f"Steps to Review IDR and Questionnaire Responses:\n{audit_steps}")
            if data_name:
                risk_parts.append(f"Name of the Data:\n{data_name}")
            if data_source:
                risk_parts.append(f"Source of the Data:\n{data_source}")
            if guidelines:
                risk_parts.append(f"Additional Guidelines:\n{guidelines}")

            risk_text = "\n\n".join(risk_parts)

            # Chunk 2 — Audit Guidance (F + O)
            audit_parts: list[str] = []
            if audit_steps:
                audit_parts.append(f"Steps to Review IDR and Questionnaire Responses:\n{audit_steps}")
            if observations:
                audit_parts.append(f"Common Observations during Fieldwork:\n{observations}")

            audit_text = "\n\n".join(audit_parts)

            # Chunk 3 — Sampling Guidance (I + J + K)
            sampling_parts: list[str] = []
            if data_calcs:
                sampling_parts.append(f"Data Calculations:\n{data_calcs}")
            if sampling_rat:
                sampling_parts.append(f"Sampling Rationale:\n{sampling_rat}")
            if expected_size:
                sampling_parts.append(f"Expected Sample Size:\n{expected_size}")
            sampling_text = "\n\n".join(sampling_parts)

            if risk_text:
                chunks.append(DocumentChunk(
                    chunk_id=f"{scope_id}_risk_profile",
                    document_name=_DOCUMENT_NAME,
                    source=DocumentSource.SCOPE_POLICY,
                    content=risk_text,
                    token_count=_estimate_tokens(risk_text),
                    scope_id=scope_id,
                    metadata={
                        "chunk_type": "risk_profile",
                        "sheet_name": sheet_name,
                        "scope_area": scope_id,
                    },
                ))

            if audit_text:
                chunks.append(DocumentChunk(
                    chunk_id=f"{scope_id}_audit_guidance",
                    document_name=_DOCUMENT_NAME,
                    source=DocumentSource.SCOPE_POLICY,
                    content=audit_text,
                    token_count=_estimate_tokens(audit_text),
                    scope_id=scope_id,
                    metadata={
                        "chunk_type": "audit_guidance",
                        "sheet_name": sheet_name,
                        "scope_area": scope_id,
                    },
                ))

            if sampling_text:
                chunks.append(DocumentChunk(
                    chunk_id=f"{scope_id}_sampling_guidance",
                    document_name=_DOCUMENT_NAME,
                    source=DocumentSource.SCOPE_POLICY,
                    content=sampling_text,
                    token_count=_estimate_tokens(sampling_text),
                    scope_id=scope_id,
                    metadata={
                        "chunk_type": "sampling_guidance",
                        "sheet_name": sheet_name,
                        "scope_area": scope_id,
                    },
                ))

            logger.info(
                "[CPRScopeExtractor] Extracted chunks | scope=%s | sheet='%s' | risk=%s | audit=%s",
                scope_id, sheet_name, bool(risk_text), bool(audit_text),
            )

        wb.close()
        logger.info(
            "[CPRScopeExtractor] Done | total_chunks=%d | skipped_sheets=%s",
            len(chunks), skipped if skipped else "none",
        )
        return chunks
