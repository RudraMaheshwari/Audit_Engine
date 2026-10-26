"""Direction-2: append source-recommended risk types to the scope areas they genuinely concern.

Five sources are considered across the 48 FCPA scope areas:
  - "SI History"       — si_history index
  - "Legal"            — legal index
  - "Additional Input" — additional_input index
  - "Past Audit"       — Output E (summarised prior reviews, last 3 years)
  - "Management"       — questionnaire index

Relevance discipline (IMPORTANT): a naive per-scope vector search returns the SAME generic excerpts for
every scope area and the same risk types get sprayed across all 48 scopes.  To prevent that, each
source's distinct findings are extracted ONCE and then ROUTED to only the scope area(s) the finding
specifically concerns.  A scope area that has no genuinely matching finding gets NO row.  Generic
findings that do not clearly concern any specific scope area are dropped rather than attached
everywhere.  This applies to ALL LLM-extracted sources — SI History, Legal, Additional Input, and Management.  For
Management specifically, only the risks management THEMSELVES noted (their opinion-of-risk answers) are
taken — not per-question documentation gaps or neutral descriptions of normal activities.

Best-effort: returns [] if the indexes/LLM are unavailable (never aborts AI Scope).
Sources never score or re-band a risk.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.config.constants import SCOPE_ID_TO_DISPLAY_NAME
from src.utils.logger import get_logger

from .config import AIScopeConfig
from .output_e_loader import load_output_e
from .scope_key import normalize

logger = get_logger(__name__)

_SOURCE_SCAN_TOP_K = 25  # SI/Legal: pull the whole (small) source doc in one shot, not per-scope
_DIRECTION2_WORKERS = 6
_MAX_HIT_CHARS = 600
_COVERAGE_DIGEST_CHARS = 220
_NO_CATEGORY = "-"
_SOURCE_SCAN_QUERY = "compliance risk findings issues observations concerns across all scope areas"

_ROUTE_SYSTEM_PROMPT = (
    "You map risk findings from a {source_label} report to the specific FCPA audit scope areas they "
    "genuinely concern.\n"
    "Rules:\n"
    "1. Extract the DISTINCT risk types / issues raised in the source excerpts. Do not invent risks "
    "that are not in the excerpts.\n"
    "2. Assign a finding to a scope area ONLY if the finding specifically and genuinely concerns that "
    "scope area's activity. A finding may concern one scope area, a few, or none.\n"
    "3. Do NOT attach a generic finding to unrelated scope areas. If a finding does not clearly "
    "concern any listed scope area, OMIT it entirely — never spray it across all scopes.\n"
    "4. Omit a (finding, scope area) pair if that risk is already reflected in that scope area's "
    "existing coverage.\n"
    "5. Use the scope-area names EXACTLY as given in the list. Preserve all named entities, program "
    "names, and abbreviations exactly as they appear in the source."
)

_ROUTE_USER_TEMPLATE = (
    "CANONICAL SCOPE AREAS (use these exact names):\n{scope_list}\n\n"
    "EXISTING COVERAGE PER SCOPE AREA (skip risks already covered here):\n{coverage_digest}\n\n"
    "SOURCE EXCERPTS ({source_label}):\n{excerpts}\n\n"
    'Return ONLY JSON of the form: '
    '{{"assignments": [{{"risk_type": "<short risk type>", '
    '"scope_areas": ["<exact scope area name>", ...]}}, ...]}}'
)

# Management risks come from the risk-OPINION questionnaire responses (what management THEMSELVES named
# as the company's key risks). Retrieved via TARGETED risk-opinion seeds (merged + de-duped) so only
# risk-opinion Q&A is scanned — NOT every question/response — then routed to the scope area(s) each
# concerns. (The whole questionnaire index is large, so a single blunt top-k would drag in general Q&A.)
# Same seed set the Scoping Deck's "Risks noted by Management" slide uses, so AI Scope surfaces the
# SAME management-noted risks (generic risk-opinion phrasing — nothing audit-specific).
_MGMT_SCAN_QUERIES = (
    "what do you consider the biggest compliance or FCPA risks in the company",
    "key risk areas or exposures management wants considered in the review",
    "corruption, bribery, or fraud concerns noted by the business respondent",
    "areas of concern, weaknesses, or vulnerabilities the respondent highlighted",
    "what keeps you up at night; where is the company most exposed to compliance risk",
    "which HCC activities do you consider the biggest risk to the company, country, or region",
    "which business units or product lines generate the biggest HCC or FCPA risk and why",
    "the biggest expenses or payment types that pose FCPA risk, e.g. payments to HCPs, hospitals, or government agencies",
    "regional, upcoming, or focus-area compliance activities or projects that may impact risk",
    "vendors or third parties the respondent thinks may be a corruption concern",
)
_MGMT_SCAN_TOP_K = 8  # per seed; merged + de-duped across seeds (targeted, not a blunt single top-k)

_MGMT_ROUTE_SYSTEM_PROMPT = (
    "You review an audited entity's MANAGEMENT questionnaire responses and identify the risks that "
    "MANAGEMENT THEMSELVES NOTED — the risks, concerns, or areas of exposure the business respondents "
    "named as a risk or concern for the company (their own view of risk).\n"
    "Rules:\n"
    "1. Identify ALL the distinct risks management NOTED — there are usually several (typically 4 to 8). "
    "Include each genuine management-noted risk (their stated view of risk / concern). Do NOT list "
    "neutral descriptions of normal activities, processes, or roles; do NOT manufacture per-question "
    "documentation gaps or 'no response' items into risks; do NOT invent risks absent from the "
    "excerpts. If management noted no risks, return an empty assignments list.\n"
    "2. Route each noted risk to the scope area(s) it genuinely concerns — one or a few — by its "
    "PRIMARY subject / control, NOT a superficial keyword overlap. Do NOT route a risk to a scope "
    "merely because they share a word (e.g. an activity term that appears in several scopes); route it "
    "to the scope whose CORE activity or control the risk is actually about. ALWAYS place a noted risk "
    "on its most relevant scope area EVEN IF that scope already has other coverage — this is the "
    "MANAGEMENT view and must be shown; do NOT drop it because it overlaps existing coverage.\n"
    "3. If a noted risk does not clearly concern any listed scope area, OMIT it — never spray it across "
    "scopes.\n"
    "4. Use the scope-area names EXACTLY as listed. Preserve all named entities, program names, and "
    "abbreviations exactly as they appear."
)


def _build_retriever(config: AIScopeConfig):
    """Build a per-auditor KnowledgeRetriever over the main KB indexes. Returns None on failure."""
    try:
        from src.infrastructure.embeddings import AzureOpenAIEmbeddingProvider
        from src.knowledge_base import KnowledgeRetriever
        from src.vector_store.index_manager import VectorIndexManager

        settings = config.settings
        index_manager = VectorIndexManager(settings, AzureOpenAIEmbeddingProvider(settings))
        return KnowledgeRetriever(index_manager, settings.auditor_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AIScope] Could not build KB retriever for Direction-2 — skipping | %s", exc)
        return None


def _hit_source_label(h) -> str:
    """'<source table/file> | Q<question id>' for a questionnaire hit; '' when metadata is absent."""
    meta = getattr(h, "metadata", None) or {}
    file = (getattr(h, "document_name", "") or meta.get("source_table", "") or "").strip()
    qid = str(meta.get("question_id", "") or "").strip()
    bits = []
    if file:
        bits.append(file)
    if qid:
        bits.append(f"Q{qid}")
    return " | ".join(bits)


def _excerpts(hits, with_header: bool = False) -> str:
    seen: set[str] = set()
    parts: list[str] = []
    for h in hits:
        text = (h.content or "")[:_MAX_HIT_CHARS].strip()
        if text and text not in seen:
            seen.add(text)
            if with_header:
                label = _hit_source_label(h)
                if label:
                    text = f"[SOURCE: {label}]\n{text}"
            parts.append(text)
    return "\n---\n".join(parts)


def _strip_fence(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return raw


def _coverage_digest(covered_summary: dict[str, str]) -> str:
    """Compact per-scope coverage map so the router can skip risks already covered. '(none)' when empty."""
    lines = []
    for scope, summary in covered_summary.items():
        flat = " ".join((summary or "").split())
        if flat:
            lines.append(f"- {scope}: {flat[:_COVERAGE_DIGEST_CHARS]}")
    return "\n".join(lines) if lines else "(no existing coverage)"


def _route_source_findings(
    llm: BaseChatModel,
    source_label: str,
    hits,
    scope_names: list[str],
    covered_summary: dict[str, str],
    system_prompt: str = _ROUTE_SYSTEM_PROMPT,
    capture_quote: bool = False,
) -> dict[str, list[tuple[str, str]]]:
    """Extract a source's distinct findings once and route each to only the scope area(s) it concerns.

    Returns {canonical_scope_display_name: [(risk_type, evidence), ...]}. `evidence` is the verbatim
    supporting question-and-response excerpt when `capture_quote` is set (used for Management), else "".
    Findings that match no listed scope area (or whose matched names don't resolve) are dropped.
    """
    excerpts = _excerpts(hits, with_header=capture_quote)
    if not excerpts:
        return {}

    # Normalised canonical lookup so the model's returned names resolve regardless of minor spelling.
    canonical = {normalize(name): name for name in scope_names}

    quote_rule = (
        '\nFor EACH assignment ALSO include "source_quote": copied VERBATIM from the SOURCE EXCERPTS '
        'above where management stated this risk. INCLUDE the bracketed "[SOURCE: <file> | Q<id>]" header '
        'line that precedes the excerpt AND the "Q: … Response: …" wording, exactly as written — so the '
        'evidence names the source file and question. If no single excerpt states it, use the closest '
        'supporting one together with its [SOURCE: …] header.'
        if capture_quote else ""
    )
    user_msg = _ROUTE_USER_TEMPLATE.format(
        scope_list="\n".join(f"- {n}" for n in scope_names),
        coverage_digest=_coverage_digest(covered_summary),
        source_label=source_label,
        excerpts=excerpts,
    ) + quote_rule
    try:
        resp = llm.invoke([
            SystemMessage(content=system_prompt.format(source_label=source_label)),
            HumanMessage(content=user_msg),
        ])
        assignments = json.loads(_strip_fence(resp.content or "")).get("assignments", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AIScope] %s routing failed — source skipped | %s", source_label, exc)
        return {}

    routed: dict[str, list[tuple[str, str]]] = {}
    dropped = 0
    for item in assignments:
        if not isinstance(item, dict):
            continue
        risk_type = str(item.get("risk_type", "")).strip()
        scope_areas = item.get("scope_areas") or []
        if not risk_type or not isinstance(scope_areas, list):
            continue
        quote = str(item.get("source_quote", "") or "").strip() if capture_quote else ""
        matched = False
        for raw_scope in scope_areas:
            name = canonical.get(normalize(str(raw_scope)))
            if name is None:
                continue
            bucket = routed.setdefault(name, [])
            if risk_type.lower() not in {rt.lower() for rt, _ in bucket}:
                bucket.append((risk_type, quote))
            matched = True
        if not matched:
            dropped += 1

    placed = sum(len(v) for v in routed.values())
    logger.info(
        "[AIScope][Direction-2] %s routed | findings placed=%d across %d scope(s) | generic/unmatched dropped=%d",
        source_label, placed, len(routed), dropped,
    )
    return routed


def _retrieve_management_hits(retriever) -> list:
    """Retrieve management's risk-OPINION Q&A via the seed queries, merged + de-duped by chunk_id.

    Targeted retrieval (several risk-opinion seeds, not one blunt top-k over the whole questionnaire
    index) so only management's risk-opinion responses are scanned — not every question/response."""
    merged: dict[str, object] = {}
    for query in _MGMT_SCAN_QUERIES:
        try:
            hits = retriever.questionnaire_for(query, top_k=_MGMT_SCAN_TOP_K)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AIScope] Management retrieval failed for seed '%s' | %s", query[:40], exc)
            continue
        for h in hits:
            content = (getattr(h, "content", "") or "").strip()
            key = getattr(h, "chunk_id", "") or content[:120]
            if key and content and key not in merged:
                merged[key] = h
    return list(merged.values())


def _past_evidence(obs) -> str:
    """Summarise an Output E past-audit observation into a Source Evidence cell (issue + source details)."""
    body = (obs.issue_type or "").strip()
    desc = (obs.issue_description or "").strip()
    if desc:
        body = f"{body} — {desc}" if body else desc
    src_bits = [b for b in ((obs.audit_report or "").strip(), (obs.report_date or "").strip()) if b]
    if src_bits:
        body = f"{body}\nSource: {', '.join(src_bits)}"
    return body.strip()


def append_source_recommended_risks(
    config: AIScopeConfig, covered_rows: list, llm: BaseChatModel, guidance=None,
) -> list:
    """Return extra AI-Scope rows (Category '-') for source-raised risk types, routed to relevant scopes.

    SI History, Legal and Additional Input findings are extracted once per source and routed only to the scope area(s)
    they genuinely concern. Management is scanned per scope area from the questionnaire index. Past
    Audit comes from the summarised Output E (last 3 years). Scope areas with no matching finding for a
    given source get no row for that source.
    """
    retriever = _build_retriever(config)
    if retriever is None:
        return []

    from .ai_scope_builder import AIScopeRow  # local import avoids a circular module dependency

    # Build lookup from display name → system coverage summary and sample size (may be absent)
    covered_summary: dict[str, str] = {}
    covered_sample: dict[str, int | None] = {}
    for row in covered_rows:
        if row.source == "System":
            covered_summary[row.scope_area] = row.summary_of_risks or ""
            covered_sample[row.scope_area] = row.proposed_sample_size

    all_scope_areas = list(SCOPE_ID_TO_DISPLAY_NAME.values())
    total = len(all_scope_areas)

    # Past Audit source comes from the summarised Output E (last 3 years). Output E's Scope Area is a
    # granular audit sub-label ("FFS - ...", "PSP - ...") or report-specific ("Proof of Service"), so it
    # won't normalize-match the 48 canonical names. Route each observation to its canonical scope via the
    # Audit Report filename token (FFS / PSP / SPO), falling back to a direct name match on the sub-label.
    canonical_lookup = {normalize(n): n for n in all_scope_areas}

    def _find(*needles: str) -> str | None:
        for n in all_scope_areas:
            k = normalize(n)
            if any(nd in k for nd in needles):
                return n
        return None

    _PAST_ROUTING = [
        (("ffs", "feeforservice", "doctorconsulting"), _find("feeforservice", "doctorconsulting")),
        (("psp", "patientsupport"), _find("patientsupport", "valueadd")),
        (("spo", "sponsor"), _find("sponsor")),
    ]

    def _past_canonical(scope_area: str, audit_report: str) -> str | None:
        direct = canonical_lookup.get(normalize(scope_area))
        if direct:
            return direct
        hint = normalize(audit_report) + normalize(scope_area)
        for needles, name in _PAST_ROUTING:
            if name and any(nd in hint for nd in needles):
                return name
        return None

    past_by_scope: dict[str, list[tuple[str, str]]] = {}
    past_dropped = 0
    try:
        for obs in load_output_e(config):
            name = _past_canonical(obs.scope_area, obs.audit_report)
            if name:
                past_by_scope.setdefault(normalize(name), []).append((obs.issue_type, _past_evidence(obs)))
            else:
                past_dropped += 1
        logger.info(
            "[AIScope][Direction-2] Past Audit routed from Output E | scopes=%d | unmapped dropped=%d",
            len(past_by_scope), past_dropped,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AIScope] Output E (Past Audit) load failed — Past Audit source skipped | %s", exc)

    # --- SI History, Legal & Additional Input: extract once, route to relevant scopes (prevents cross-scope spray) ---
    def _route(label: str, retrieve_fn) -> dict[str, list[str]]:
        try:
            hits = retrieve_fn(_SOURCE_SCAN_QUERY, top_k=_SOURCE_SCAN_TOP_K)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AIScope] %s retrieval failed — source skipped | %s", label, exc)
            return {}
        return _route_source_findings(llm, label, hits, all_scope_areas, covered_summary)

    si_by_scope = _route("SI History", retriever.si_history_for)
    legal_by_scope = _route("Legal", retriever.legal_for)
    additional_by_scope = _route("Additional Input", retriever.additional_input_for)

    # --- Management: the risks management THEMSELVES noted (opinion-of-risk), extracted ONCE from the
    # questionnaire index and routed to only the scope area(s) each concerns — same discipline as SI/
    # Legal (NOT a per-scope scan, which sprayed concerns across all 48 scopes). ---
    mgmt_hits = _retrieve_management_hits(retriever)
    # 0 hits ⇒ questionnaire index unpopulated for this auditor OR the auditor_id filter doesn't match
    # what was ingested (the questionnaire schema filters on its own `auditor_id`, not `auditor_folder`).
    logger.info(
        "[AIScope][Direction-2] Management | auditor_id=%r | questionnaire hits=%d%s",
        config.settings.auditor_id, len(mgmt_hits),
        "" if mgmt_hits else " (index empty or auditor_id filter mismatch — no Management rows will appear)",
    )
    # Pass NO coverage for Management: its noted risks must appear even where System already covers the
    # scope (it is the entity's own stated view, a distinct source). Coverage-skip stays for SI/Legal.
    mgmt_by_scope = _route_source_findings(
        llm, "Management", mgmt_hits, all_scope_areas, {},
        system_prompt=_MGMT_ROUTE_SYSTEM_PROMPT,
        capture_quote=True,   # Management rows carry the exact questionnaire Q & Response as evidence
    )

    logger.info(
        "[AIScope][Direction-2] Scanning %d scope areas | SI History, Legal, Additional Input & Management "
        "routed from source; Past Audit from Output E | %d parallel workers…",
        total, _DIRECTION2_WORKERS,
    )

    def _rows(scope_area: str, source: str, sample_size, items: list) -> list:
        """items: list of (risk_type, source_evidence) tuples."""
        return [
            AIScopeRow(
                scope_area=scope_area, overall_risk_category=_NO_CATEGORY,
                proposed_sample_size=sample_size, summary_of_risks=rt, source=source,
                source_evidence=ev,
            )
            for rt, ev in items
        ]

    def _sample_for(scope_area: str):
        """Sample size for a Direction-2 row: the System row's value, else the CPR guidance value.

        Direction-2 can surface risks in scopes the system never flagged (no System row), so fall back
        to the CPR guidance workbook — which carries a sample size for every scope area.
        """
        sample = covered_sample.get(scope_area)
        if sample is None and guidance is not None:
            try:
                sample = guidance.lookup(scope_area)
            except Exception:  # noqa: BLE001
                sample = None
        return sample

    def _scan(item: tuple[int, str]) -> list:
        i, scope_area = item
        sample_size = _sample_for(scope_area)
        key = normalize(scope_area)
        out: list = []

        # SI History / Legal / Additional Input / Management: (risk_type, evidence) routed to THIS scope.
        si_rt = si_by_scope.get(scope_area, [])
        legal_rt = legal_by_scope.get(scope_area, [])
        additional_rt = additional_by_scope.get(scope_area, [])
        mgmt_rt = mgmt_by_scope.get(scope_area, [])

        # Past Audit from Output E — (risk_type, evidence), de-duplicated by risk type.
        pa_items: list = []
        seen: set[str] = set()
        for rt, ev in past_by_scope.get(key, []):
            if rt and rt.lower() not in seen:
                seen.add(rt.lower())
                pa_items.append((rt, ev))

        out += _rows(scope_area, "SI History", sample_size, si_rt)
        out += _rows(scope_area, "Legal", sample_size, legal_rt)
        out += _rows(scope_area, "Additional Input", sample_size, additional_rt)
        out += _rows(scope_area, "Management", sample_size, mgmt_rt)
        out += _rows(scope_area, "Past Audit", sample_size, pa_items)

        if si_rt or legal_rt or additional_rt or mgmt_rt or pa_items:
            logger.info(
                "[AIScope][Direction-2] (%d/%d) scope=%r | SI History=%d | Legal=%d | Additional Input=%d | Management=%d | Past Audit=%d",
                i, total, scope_area, len(si_rt), len(legal_rt), len(additional_rt), len(mgmt_rt), len(pa_items),
            )
        return out

    extra: list = []
    with ThreadPoolExecutor(max_workers=_DIRECTION2_WORKERS) as pool:
        for rows in pool.map(_scan, enumerate(all_scope_areas, start=1)):
            extra.extend(rows)

    logger.info("[AIScope] Direction-2 appended %d source-recommended row(s) across %d scope areas", len(extra), total)
    return extra
