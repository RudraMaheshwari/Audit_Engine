from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.config.constants import HCC_PDF_FILENAME, HCBI_PDF_FILENAME

_EFFECTIVE_DATE_PATTERNS = [
    re.compile(r"(?:Effective|Issue|Approved|Revised|Review)\s+Date[:\s]+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})", re.IGNORECASE),
    re.compile(r"(?:Effective|Issue|Approved|Revised|Review)\s+Date[:\s]+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", re.IGNORECASE),
    re.compile(r"Effective[:\s]+(\d{4}[\/\-]\d{2}[\/\-]\d{2})", re.IGNORECASE),
    re.compile(r"Version\s+Date[:\s]+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})", re.IGNORECASE),
    re.compile(r"\bDate\b[:\s]+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})", re.IGNORECASE),
]

_POLICY_NUMBER_RE   = re.compile(r"(TV-(?:POL|SOP|STD|WI|FOR|QR)-\d{4,6})", re.IGNORECASE)
_VERSION_STEM_RE    = re.compile(r"_v(\d+(?:\.\d+)+)", re.IGNORECASE)
_VERSION_CONTENT_RE = re.compile(r"\bVersion[:\s]+v?(\d+(?:\.\d+)+)", re.IGNORECASE)
_POLICY_OWNER_RE    = re.compile(r"(?:Policy|Document)\s+Owner[:\s]+([A-Za-z][^\n]{2,80}?)(?:\n|$)", re.IGNORECASE)
_POLICY_TITLE_RE    = re.compile(r"^(?:POLICY\s+TITLE|DOCUMENT\s+TITLE|TITLE)[:\s]+(.+)$", re.MULTILINE | re.IGNORECASE)

_SECTION_KEYWORD_RE = re.compile(
    r"^(?:Section|Part|Article|Clause|Chapter)\s+(\d+(?:\.\d+)*)\s*[:\-\.]?\s*(.*)", re.IGNORECASE
)
_SECTION_NUMERIC_RE = re.compile(r"^(\d+(?:\.\d+)+)[.\s]\s+(.+)")

_DATE_FORMATS = [
    "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y-%m-%d",
    "%B %d, %Y", "%B %d %Y", "%d %B %Y", "%d %B, %Y",
    "%b %d, %Y",  "%d %b %Y", "%d/%m/%y", "%m/%d/%y",
]


class MetadataExtractor:
    """
    Stateless helper — instantiate once, reuse across all documents.
    All public methods return plain dicts so callers can merge freely.
    """

    def extract_from_filename(self, file_path: Path) -> dict:
        stem = file_path.stem
        result: dict = {}

        m = _POLICY_NUMBER_RE.search(stem)
        if m:
            result["policy_number"] = m.group(1).upper()

        m = _VERSION_STEM_RE.search(stem)
        if m:
            result["version"] = f"v{m.group(1)}"

        if file_path.name == HCBI_PDF_FILENAME:
            result.setdefault("policy_domain", "HCBI")
            result.setdefault("policy_name", "Healthcare Business Integrity Guide")
        elif file_path.name == HCC_PDF_FILENAME:
            result.setdefault("policy_domain", "HCC")
            result.setdefault("policy_name", "HCC Framework")

        return result

    def extract_header_metadata(self, content: str) -> dict:
        header = content[:3000]
        result: dict = {}

        for pat in _EFFECTIVE_DATE_PATTERNS:
            m = pat.search(header)
            if m:
                result["effective_date"] = self._normalise_date(m.group(1))
                break

        m = _VERSION_CONTENT_RE.search(header)
        if m:
            result.setdefault("version", f"v{m.group(1)}")

        m = _POLICY_OWNER_RE.search(header)
        if m:
            result["policy_owner"] = m.group(1).strip().rstrip(",;:")

        m = _POLICY_TITLE_RE.search(header)
        if m:
            result.setdefault("policy_name", m.group(1).strip())

        return result

    def enrich(self, file_path: Path, content: str, extra: dict | None = None) -> dict:
        meta = self.extract_from_filename(file_path)
        for k, v in self.extract_header_metadata(content).items():
            if k not in meta:
                meta[k] = v
        if extra:
            meta.update(extra)
        return {k: v for k, v in meta.items() if v is not None}

    @staticmethod
    def parse_section_header(header_text: str) -> tuple[Optional[str], str]:
        text = header_text.strip()

        m = _SECTION_KEYWORD_RE.match(text)
        if m:
            title_part = m.group(2).strip() or text
            return m.group(1), title_part

        m = _SECTION_NUMERIC_RE.match(text)
        if m:
            return m.group(1), m.group(2).strip()

        return None, text

    @staticmethod
    def _normalise_date(raw: str) -> str:
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return raw.strip()
