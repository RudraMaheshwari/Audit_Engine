"""Past Audit Summarizer Agent — extracts per-observation records from a past audit report."""

import json
import re
from datetime import datetime

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.prompts.past_audit_summary_prompt import (
    PAST_AUDIT_SUMMARY_SYSTEM,
    PAST_AUDIT_SUMMARY_USER,
)
from src.schemas.past_audit import PastAuditObservation
from src.utils.logger import get_logger

logger = get_logger(__name__)

_NOT_AVAILABLE = "Not Available"

# Date formats tried (day-first, matching AU/NZ audit reports) before falling back.
_DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d",
    "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%B-%Y", "%b %Y", "%B %Y",
)


def _normalize_report_date(raw: str) -> str:
    """Normalise a free-text report date to a single canonical format (YYYY-MM-DD).

    A bare year stays as 'YYYY'; an unparseable value keeps the first 4-digit year it contains, or
    the original text if none. 'Not Available'/blank is preserved so downstream logic is unaffected."""
    s = (raw or "").strip()
    if not s or s.lower() in ("not available", "n/a", "na", "none", "unknown", "-"):
        return _NOT_AVAILABLE
    if re.fullmatch(r"(19|20)\d{2}", s):          # bare year
        return s
    try:                                           # dateutil handles most formats (day-first for AU/NZ)
        from dateutil import parser as _dateparser
        return _dateparser.parse(s, dayfirst=True).strftime("%Y-%m-%d")
    except Exception:                              # noqa: BLE001
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    m = re.search(r"(19|20)\d{2}", s)
    return m.group() if m else s


class PastAuditSummarizerAgent:
    """Turns one past audit report's text into a list of PastAuditObservation records."""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def summarize(self, document_name: str, content: str) -> list[PastAuditObservation]:
        """Extract observations from a single report's reassembled text."""
        if not content or not content.strip():
            return []
        user_msg = PAST_AUDIT_SUMMARY_USER.format(document_name=document_name, content=content)
        try:
            response = self._llm.invoke([
                SystemMessage(content=PAST_AUDIT_SUMMARY_SYSTEM),
                HumanMessage(content=user_msg),
            ])
            records = self._parse(response.content)
        except Exception as exc:
            logger.warning(
                "[PastAuditSummarizer] summary failed | doc=%s | error=%s", document_name, exc
            )
            return []

        return [self._to_observation(document_name, r) for r in records]

    @staticmethod
    def _parse(content: str) -> list[dict]:
        raw = (content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = json.loads(raw)
        observations = parsed.get("observations", []) if isinstance(parsed, dict) else []
        return [o for o in observations if isinstance(o, dict)]

    @staticmethod
    def _to_observation(document_name: str, record: dict) -> PastAuditObservation:
        def _val(key: str) -> str:
            value = (record.get(key) or "").strip()
            return value or _NOT_AVAILABLE

        return PastAuditObservation(
            source_document=document_name,
            report_date=_normalize_report_date(record.get("report_date") or ""),
            scope_area=_val("scope_area"),
            issue_type=(record.get("issue_type") or "").strip(),
            issue_description=(record.get("issue_description") or "").strip(),
            criticality=_val("criticality"),
            audit_report=document_name,
        )
