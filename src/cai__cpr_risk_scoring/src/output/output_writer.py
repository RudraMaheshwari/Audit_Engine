import json
import logging
import re
from pathlib import Path
from typing import Optional

from src.config.constants import (
    MANUAL_REVIEW_LABEL,
    RISK_CATEGORY_NONE,
    RISK_CATEGORY_NOT_CATEGORIZED,
    SCOPE_ID_TO_DISPLAY_NAME,
    SCOPE_UNCLEAR_LABEL,
    SHEET_A_NAME,
)
from src.models.schemas import (
    AuditState,
    OutputRow,
    RiskLevel,
    ScopeAreaSummary,
)
from src.repositories.output_repo import OutputRepository
from src.schemas.state import RiskOutput, RiskType

logger = logging.getLogger(__name__)


def _display_scope(value: str) -> str:
    """Map any raw scope id(s) (e.g. ONE_TIME_VENDOR) to the canonical display name. Handles the
    '; '-joined multi-scope form and passes through values already in display form. A deterministic
    safety net so a raw CAPS_WITH_UNDERSCORE scope can never leak into the Scope Area column, even if
    the writer LLM echoes the id instead of the provided display name."""
    value = (value or "").strip()
    if not value:
        return value
    parts = [p.strip() for p in value.split(";") if p.strip()]
    mapped = [SCOPE_ID_TO_DISPLAY_NAME.get(p, p) for p in parts]
    return "; ".join(mapped)


_RISK_TYPE_TO_LEVEL = {
    RiskType.POTENTIAL_RISK: RiskLevel.HIGH,
    RiskType.FURTHER_REVIEW: RiskLevel.MEDIUM,
    RiskType.NO_RISK:        RiskLevel.NO_RISK,
}

_ANALYZER_LABEL_RE = re.compile(
    r"^\s*\**\s*(Risk Summary|Risk Type|Risk Description|Risk Rationale|Risk Category Reasoning|"
    r"Risk Category|Scope Area Reasoning|Scope Area|Document Utilized[^:\n]*)\s*\**\s*:\s*",
    re.MULTILINE | re.IGNORECASE,
)

_CATEGORY_TO_SCORE: dict[str, float] = {
    "high risk":   8.5,
    "medium risk": 6.0,
    "low risk":    3.0,
    "no risk":     1.0,
}


# Authoritative policy-family label per policy FILE (identity-driven). The "Source:" type is otherwise
# assigned from whichever index/retrieval block surfaced the chunk, so a scope-area / HCC policy ingested
# into the HCBI index gets mislabeled "HCBI Guide (Global Policy)". This map corrects the label from the
# document's TRUE family, keyed on the file stem (version-insensitive). Extend as the policy set grows —
# only add a file here when its correct family is confirmed.
_POLICY_FAMILY_BY_FILE: dict[str, str] = {
    "TV-POL-02257": "HCBI Guide (Global Policy)",   # the CBI / HCBI Guide
    "TV-POL-02312": "HCC Policy (Global Policy)",    # HCC Policy — NOT the HCBI Guide
}

_FILE_NAME_RE = re.compile(r"File Name:\s*([^;\n]+)", re.IGNORECASE)
_SOURCE_PREFIX_RE = re.compile(r"^Source:\s*[^;\n]+", re.IGNORECASE)


# A dashboard/transactional TABLE cited as a "File Name" (e.g. gld__..._dashboard__cpr) — it is NOT a
# supporting document. These must be labelled "Transactional Dashboard", never "Supporting Evidence".
_DASHBOARD_TABLE_RE = re.compile(r"\bgld__[a-z0-9_]+__cpr\b|dashboard", re.IGNORECASE)


def _correct_source_family(entry: str) -> str:
    """Rewrite an entry's 'Source: <family>' prefix to the file's authoritative family when the cited
    File Name matches a known policy file — so the label reflects the document's identity, not the index
    it happened to be retrieved from. A dashboard/transactional table name is forced to "Transactional
    Dashboard" (never Supporting Evidence). Unknown files are left exactly as written."""
    match = _FILE_NAME_RE.search(entry)
    if not match:
        return entry
    fname = match.group(1).strip().lower()
    for key, family in _POLICY_FAMILY_BY_FILE.items():
        if key.lower() in fname:
            return _SOURCE_PREFIX_RE.sub(f"Source: {family}", entry, count=1)
    if _DASHBOARD_TABLE_RE.search(fname):
        return _SOURCE_PREFIX_RE.sub("Source: Transactional Dashboard", entry, count=1)
    return entry


def _norm_fname(name: str) -> str:
    """Normalise a file name for tolerant matching: lowercase, drop extension + trailing '(1)'."""
    n = (name or "").lower().strip()
    n = re.sub(r"\.(pdf|xlsx?|docx?|pptx?|csv|json|txt)$", "", n)
    n = re.sub(r"\s*\(\d+\)\s*$", "", n)   # trailing duplicate marker like " (1)"
    return re.sub(r"\s+", " ", n).strip()


def _is_phantom_supporting(entry: str, valid_files: set[str]) -> bool:
    """True when a Supporting-Evidence entry cites a file that is NOT among the auditor's indexed docs.
    Lenient on purpose — keeps the citation on ANY reasonable name match, so a real file is never
    dropped over a minor spelling difference (we prefer keeping over wrongly removing evidence)."""
    low = " ".join(entry.lower().split())
    if not low.startswith("source: supporting evidence"):
        return False
    m = re.search(r"file name:\s*([^;]+)", low)
    if not m:
        return False
    cited = _norm_fname(m.group(1))
    if not cited:
        return False
    return not any(cited == v or cited in v or v in cited for v in valid_files)


def _source_dedup_key(entry: str) -> str:
    """Key used to collapse duplicate 'Source:' entries. For Supporting Evidence (no section/page),
    key on the file name only so the same document never appears twice; for everything else key on
    the whole entry so distinct policy sections/pages are kept."""
    low = " ".join(entry.lower().split())
    if low.startswith("source: supporting evidence"):
        m = re.search(r"file name:\s*([^;]+)", low)
        if m:
            return "supporting::" + m.group(1).strip()
    return low


def _normalize_source_details(text: str, valid_supporting_files: set[str] | None = None) -> str:
    """Put every 'Source:' entry on its own bulleted line, regardless of how the LLM joined
    them (';', '|', or bullets). Keeps each entry's internal 'File Name: …; Section: …; Page No.: …'.
    Also corrects the 'Source:' family label for known policy files (see _POLICY_FAMILY_BY_FILE).
    When `valid_supporting_files` is provided (the auditor's actually-indexed doc names), drops any
    Supporting-Evidence citation whose file is not among them (phantom/hallucinated file)."""
    if not text or "Source:" not in text:
        return text
    entries = []
    seen: set[str] = set()
    for part in re.split(r"(?=Source:)", text):
        part = part.strip().lstrip("•").strip().strip("|").strip().rstrip(";").strip()
        if not part.startswith("Source:"):
            continue
        corrected = _correct_source_family(part)
        # Drop a phantom Supporting-Evidence file (cited but not in the auditor's index). Fail-open:
        # only filters when the indexed-file set is actually available/non-empty.
        if valid_supporting_files and _is_phantom_supporting(corrected, valid_supporting_files):
            continue
        # De-duplicate: the same evidence source must appear ONCE, even if the model repeated it.
        # Supporting-evidence files carry no section/page, so the SAME file cited twice (with any
        # trailing variation) must collapse to one — key on the file name alone. Policy entries keep
        # full-entry keying so distinct sections/pages of the same policy file are preserved.
        key = _source_dedup_key(corrected)
        if key in seen:
            continue
        seen.add(key)
        entries.append("• " + corrected)
    return "\n".join(entries) if entries else text


def _label_key(label: str) -> str | None:
    l = label.strip().lower()
    if l.startswith("risk summary"):              return "risk_summary"
    if l.startswith("risk type"):                 return "risk_type_class"
    if l.startswith("risk description"):          return "risk_description"
    if l.startswith("risk rationale"):            return "risk_rationale"
    if l.startswith("risk category reasoning"):   return "risk_category_reasoning"
    if l.startswith("risk category"):             return "risk_category"
    if l.startswith("scope area reasoning"):       return "scope_area_reasoning"
    if l.startswith("scope area"):                return "scope_area"
    if l.startswith("document utilized"):         return "document_utilized"
    return None


def _parse_analyzer_output(text: str) -> dict:
    result: dict[str, str] = {}
    if not text:
        return result
    matches = list(_ANALYZER_LABEL_RE.finditer(text))
    for i, m in enumerate(matches):
        key = _label_key(m.group(1))
        if not key:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        value = text[start:end].strip().strip("*").strip()
        if key not in result:
            result[key] = value
    return result


def _normalize_title(cls_text: str, full_text: str = "") -> str:
    blob = (cls_text or "").lower()
    if not cls_text:
        blob += " " + (full_text[:400] or "").lower()
    if "potential risk" in blob:
        return "Potential Risk"
    if "further review" in blob:
        return "Further Review Required"
    return "No Risk Identified"


def _short_label(description: str) -> str:
    if not description:
        return ""
    first = re.split(r"(?<=[.!?])\s", description.strip(), maxsplit=1)[0]
    return first[:140].strip()


def _looks_like_input_echo(text: str) -> bool:
    if not text:
        return False
    head = text[:600].upper()
    markers = ("AUDIT QUESTION", "TPI RESPONSE", "SCOPE ID")
    if sum(m in head for m in markers) >= 2:
        return True
    if "HCBI POLICY CONTEXT" in head and "|" in head:
        return True
    return False


class OutputWriter:
    """
    Converts pipeline AuditState objects and transactional RiskOutput findings into
    OutputRow records and writes them to the Excel workbook via OutputRepository.
    Appends a scope summary sheet after all rows are written.
    """

    def __init__(self, repo: OutputRepository, settings=None) -> None:
        self._repo = repo
        self._settings = settings
        self._translator = None
        # Normalised set of the auditor's actually-indexed supporting-doc names. Empty = no filtering
        # (fail-open). Populated by the runner via set_valid_supporting_files().
        self._valid_supporting_files: set[str] = set()
        # Optional Delta mirror of the output sheets. Best-effort; disabled if no settings provided.
        self._delta = None
        if settings is not None:
            try:
                from src.repositories.delta_export import DeltaOutputExporter
                self._delta = DeltaOutputExporter(settings)
            except Exception as exc:  # noqa: BLE001
                logger.warning("OutputWriter: Delta exporter unavailable — Excel only | %s", exc)

    def set_valid_supporting_files(self, names) -> None:
        """Register the auditor's actually-indexed supporting-doc names so phantom (non-existent)
        Supporting-Evidence citations are dropped from the output. Normalised for tolerant matching."""
        self._valid_supporting_files = {_norm_fname(n) for n in (names or []) if n}

    def _local_language(self) -> str:
        return (getattr(self._settings, "audit_local_language", "") or "").strip() if self._settings else ""

    def _attach_local_rows(self, rows: list[OutputRow]) -> None:
        """Populate each row's local-language Risk Description + Risk Rationale (for BILINGUAL Output A).
        Batched ~8 findings per LLM call. No-op when audit_local_language is unset. Best-effort — a
        failure simply leaves the English-only fields (Output A then renders English for those rows)."""
        language = self._local_language()
        if not language:
            return
        targets = [r for r in rows if (getattr(r, "risk_description", "") or getattr(r, "risk_rationale", ""))]
        if not targets:
            return
        if self._translator is None:
            try:
                from src.services.translation import TranslationService
                self._translator = TranslationService(self._settings)
            except Exception as exc:  # noqa: BLE001
                logger.warning("OutputWriter: translator unavailable — Output A stays English | %s", exc)
                return
        group_size = 8
        for start in range(0, len(targets), group_size):
            group = targets[start:start + group_size]
            fields: dict[str, str] = {}
            for j, r in enumerate(group):
                fields[f"d{j}"] = r.risk_description or ""
                fields[f"r{j}"] = r.risk_rationale or ""
            local = self._translator.translate_fields_to_language(fields, language)
            if not local:
                continue
            for j, r in enumerate(group):
                if local.get(f"d{j}"):
                    r.risk_description_local = local[f"d{j}"]
                if local.get(f"r{j}"):
                    r.risk_rationale_local = local[f"r{j}"]

    def write_one(
        self,
        state: AuditState,
        sno: int,
        output_path: str | Path,
    ) -> None:
        row = self._state_to_row(state, sno=sno)
        self._attach_local_rows([row])
        self._repo.append_row(row, output_path)

    def write_manual_review(
        self,
        question,
        sno: int,
        reason: str,
        output_path: str | Path,
    ) -> None:
        """Write a "Manual Review Required" row for a question whose automated analysis failed.

        The item surfaces for a human reviewer instead of being silently dropped. These rows are
        kept out of risk aggregation (they carry no classification).
        """
        reason_text = (reason or "").strip()
        if len(reason_text) > 300:
            reason_text = reason_text[:300] + "…"
        row = OutputRow(
            sno=sno,
            source_file=question.source_table or question.questionnaire_type,
            risk_title=MANUAL_REVIEW_LABEL,
            risk_type="Automated analysis did not complete — manual review required",
            risk_category=MANUAL_REVIEW_LABEL,
            risk_category_reasoning=(
                "Automated classification did not complete for this item; a human reviewer must "
                "assess it. No risk category was derived."
            ),
            risk_rationale=(
                "The automated analysis pipeline did not produce a classification for this item "
                f"({reason_text}). Flagged for manual review; not auto-assessed."
            ),
            risk_description=(
                "Automated analysis did not complete for this question; flagged for manual review."
            ),
            scope_area=SCOPE_UNCLEAR_LABEL,
            evidence_explanation="",
            question=question.question_text,
            response=question.response_text,
            followup_question=question.followup_question,
            followup_response=question.followup_response,
            source_details="Not Available",
        )
        self._repo.append_row(row, output_path)

    def write(
        self,
        states: list[AuditState],
        scope_name: str,
        output_path: str | Path,
        analysis_findings: Optional[list[RiskOutput]] = None,
        start_sno: int = 1,
    ) -> None:
        """
        Write all questionnaire states and transactional analysis findings to the output
        workbook, then rebuild and append the scope summary sheet.
        If the file already exists, questionnaire rows are skipped (resume mode) and only
        analysis rows and the summary are appended.
        """
        path = Path(output_path)

        if path.exists():
            existing_row_count = self._count_existing_rows(path)
        else:
            existing_row_count = 0
            output_rows = [self._state_to_row(s, sno=i + start_sno) for i, s in enumerate(states)]
            if output_rows:
                self._attach_local_rows(output_rows)
                self._repo.write_excel(output_rows, scope_summary=None, output_path=path)
                existing_row_count = len(output_rows)

        analysis_rows = [
            self._risk_output_to_row(f, sno=existing_row_count + i + 1)
            for i, f in enumerate(analysis_findings or [])
        ]
        if analysis_rows:
            self._attach_local_rows(analysis_rows)
            self._repo.write_excel(analysis_rows, scope_summary=None, output_path=path)

        summary = self._build_summary_from_file(
            path, scope_name,
            scope_id=states[0].question.scope_id if states else "",
        )
        self._repo.write_summary(summary, path)

        # Mirror Output A to Delta — build the COMPLETE row set independently of resume-mode so the
        # table always reflects every questionnaire + transactional row (best-effort).
        if self._delta is not None:
            try:
                all_a_rows = [self._state_to_row(s, sno=i + start_sno) for i, s in enumerate(states)]
                all_a_rows += [
                    self._risk_output_to_row(f, sno=len(states) + i + 1)
                    for i, f in enumerate(analysis_findings or [])
                ]
                self._delta.export_output_a(all_a_rows)
            except Exception as exc:  # noqa: BLE001
                logger.warning("OutputWriter: Output A Delta export failed | %s", exc)

        logger.info(
            "OutputWriter: finalised | questionnaire=%d | analysis=%d | path=%s",
            existing_row_count, len(analysis_rows), path,
        )

    def row_for(self, state: AuditState, sno: int) -> OutputRow:
        """Public accessor: build the OutputRow for a completed state (used by aggregation)."""
        return self._state_to_row(state, sno=sno)

    def write_aggregation(self, risks: list, scope_overall: dict, output_path) -> None:
        """Write the aggregated AI Risk Analysis sheet + the grouped Risk Summary sheet."""
        if not risks:
            logger.info("OutputWriter: no aggregated risks to write — skipping aggregation sheets")
            return
        self._repo.write_aggregation_sheets(risks, scope_overall, output_path)
        if self._delta is not None:
            try:
                self._delta.export_output_c(risks, scope_overall)
            except Exception as exc:  # noqa: BLE001
                logger.warning("OutputWriter: Output C Delta export failed | %s", exc)

    def write_past_audit(self, observations: list, output_path) -> None:
        """Write the Past Audit Report sheet (one row per observation)."""
        if not observations:
            logger.info("OutputWriter: no past-audit observations — skipping Past Audit sheet")
            return
        self._repo.write_past_audit_sheet(observations, output_path)
        if self._delta is not None:
            try:
                self._delta.export_output_e(observations)
            except Exception as exc:  # noqa: BLE001
                logger.warning("OutputWriter: Output E Delta export failed | %s", exc)

    def write_scope_txn_summary(self, rows: list, output_path) -> None:
        """Write the per-scope transaction summary sheet (rows already formatted, '-' for no data)."""
        if not rows:
            logger.info("OutputWriter: no scope summary rows — skipping Scope Txn Summary sheet")
            return
        self._repo.write_scope_txn_summary(rows, output_path)
        if self._delta is not None:
            try:
                self._delta.export_output_f(rows)
            except Exception as exc:  # noqa: BLE001
                logger.warning("OutputWriter: Output F Delta export failed | %s", exc)

    def _state_to_row(self, state: AuditState, sno: int) -> OutputRow:
        """Parse the LLM risk-writer output from AuditState and map it to an OutputRow."""
        finding  = state.risk_finding
        question = state.question

        ai_text = finding.ai_recommendation or ""
        parsed = _parse_analyzer_output(ai_text)

        is_echo = _looks_like_input_echo(ai_text)
        safe_text = "" if is_echo else ai_text

        risk_title   = _normalize_title(parsed.get("risk_type_class", ""), safe_text)
        raw_category = parsed.get("risk_category", "").strip()
        description  = parsed.get("risk_description", "").strip()
        rationale    = parsed.get("risk_rationale", "").strip()
        reasoning    = parsed.get("risk_category_reasoning", "").strip()
        scope_area          = _display_scope(parsed.get("scope_area", "").strip()) or SCOPE_UNCLEAR_LABEL
        scope_area_reasoning = parsed.get("scope_area_reasoning", "").strip()
        source              = _normalize_source_details(
            parsed.get("document_utilized", "").strip(), self._valid_supporting_files)

        # Category is driven by the Risk Type (in risk_title) so the reviewer rule holds even if the
        # model emits an inconsistent band: only Potential Risk is scored (High/Medium/Low); Further
        # Review Required is always an uncategorised open item; No Risk Identified is No Risk.
        _cat_lower = raw_category.lower()
        if risk_title == "Further Review Required":
            risk_category = RISK_CATEGORY_NOT_CATEGORIZED
        elif risk_title == "No Risk Identified":
            risk_category = "No Risk"
        elif "high" in _cat_lower:
            risk_category = "High Risk"
        elif "medium" in _cat_lower:
            risk_category = "Medium Risk"
        elif "low" in _cat_lower:
            risk_category = "Low Risk"
        else:
            # Potential Risk with an unreadable band — default to High (a confirmed deviation).
            risk_category = "High Risk"

        if not description and not is_echo:
            description = ai_text.strip()

        risk_type_short = (
            parsed.get("risk_summary", "").strip()
            or _short_label(description)
            or question.question_text[:140]
        )

        conflict_note = "; ".join(
            f"Q{c.question_id_b} (similarity={c.similarity_score:.3f})"
            for c in finding.conflicts
        )
        evidence_note = (
            f"Conflict detected with: {conflict_note}"
            if conflict_note else "No cross-question conflicts detected."
        )

        return OutputRow(
            sno=sno,
            source_file=question.source_table or question.questionnaire_type,
            risk_title=risk_title,
            risk_type=risk_type_short,
            risk_category=risk_category,
            risk_category_reasoning=reasoning or description or "See Risk Description.",
            risk_rationale=rationale or description or "See Risk Description.",
            risk_description=description or "No analysis text produced.",
            scope_area=scope_area,
            scope_area_reasoning=scope_area_reasoning,
            calculation_evidence=(finding.calculation_evidence or None),
            evidence_explanation=evidence_note,
            question=question.question_text,
            response=question.response_text,
            followup_question=question.followup_question,
            followup_response=question.followup_response,
            source_details=source or "Not Available",
        )

    def _risk_output_to_row(self, finding: RiskOutput, sno: int) -> OutputRow:
        # Transactional findings cite the dashboard/table directly: "Source: <table_name>".
        # (No "File Name"/"Section" labels — those belong to document-sourced questionnaire findings.)
        source_details_str = "\n".join(
            f"• Source: {sd.document_name}"
            for sd in finding.source_details
        ) or "Not Available"
        return OutputRow(
            sno=sno,
            source_file="Transactional Analysis",
            risk_title=finding.risk_title.value,
            risk_type=finding.risk_type,
            risk_category=finding.risk_category or RISK_CATEGORY_NONE,
            risk_category_reasoning=finding.risk_score_reasoning,
            risk_rationale=finding.risk_rationale,
            risk_description=finding.risk_description,
            scope_area=_display_scope(finding.scope_area) or SCOPE_UNCLEAR_LABEL,
            calculation_evidence=finding.calculation_evidence,
            sql_queries=finding.sql_queries,
            samples=finding.samples or "",
            evidence_explanation=finding.evidence_explanation,
            question=finding.question,
            response=finding.response,
            followup_question=finding.followup_question,
            followup_response=finding.followup_response,
            source_details=source_details_str,
        )

    def _count_existing_rows(self, path: Path) -> int:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True)
            ws = wb[SHEET_A_NAME]
            count = max(ws.max_row - 1, 0)
            wb.close()
            return count
        except Exception:
            return 0

    def _build_summary_from_file(
        self,
        path: Path,
        scope_name: str,
        scope_id: str,
    ) -> ScopeAreaSummary:
        header: list[str] = []
        rows_data: list = []
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True)
            ws = wb[SHEET_A_NAME]
            all_rows = list(ws.iter_rows(values_only=True))
            wb.close()
            if all_rows:
                header = [str(h or "").strip().lower() for h in all_rows[0]]
                rows_data = all_rows[1:]
        except Exception:
            header, rows_data = [], []

        # Resolve columns by HEADER NAME, not fixed index — robust to column additions/reorders.
        def _idx(label: str):
            try:
                return header.index(label.strip().lower())
            except ValueError:
                return None

        def _cell(row, idx) -> str:
            if idx is None or idx >= len(row):
                return ""
            return str(row[idx] or "")

        idx_title     = _idx("Risk Title")
        idx_category  = _idx("Risk Category")
        idx_rationale = _idx("Risk Rationale")

        # Risk Title only ever holds three values → exactly three buckets (no "Low").
        potential = further_review = no_risk = 0
        scores = []
        gaps = []

        for row in rows_data:
            title     = _cell(row, idx_title)
            category  = _cell(row, idx_category)
            rationale = _cell(row, idx_rationale)

            if "Potential Risk" in title:
                potential += 1
            elif "Further Review" in title:
                further_review += 1
            else:  # "No Risk Identified"
                no_risk += 1

            cat_score = _CATEGORY_TO_SCORE.get(category.lower().strip())
            if cat_score is not None:
                scores.append(cat_score)

            for g in rationale.split("; "):
                if g and g not in ("None identified", "See Risk Description for policy rationale."):
                    gaps.append(g)

        total = len(rows_data)
        avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0
        top_gaps = list(dict.fromkeys(gaps))[:5]

        if potential > 0:
            overall = RiskLevel.HIGH
        elif further_review > 0:
            overall = RiskLevel.MEDIUM
        else:
            overall = RiskLevel.NO_RISK

        return ScopeAreaSummary(
            scope_id=scope_id,
            scope_name=scope_name,
            total_questions=total,
            high_risk_count=potential,
            medium_risk_count=further_review,
            low_risk_count=0,
            no_risk_count=no_risk,
            average_risk_score=avg_score,
            top_compliance_gaps=top_gaps,
            transactional_findings_summary="",
            overall_risk_rating=overall,
        )
