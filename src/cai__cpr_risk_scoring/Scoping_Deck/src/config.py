"""Configuration for the scoping deck: the shared app Settings plus deck-only knobs and paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from src.config.constants import SHEET_E_NAME, SHEET_F_NAME
from src.config.settings import Settings, get_settings

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_SHORTHAND_LOGO_NAME = "JNJ_Logo_Shorthand_Red_RGB.png"
# Opening (first) slide uses the single-line red logo in its footer; every other slide keeps the
# shorthand logo above. Resolved next to whatever logo_path is in use so it works on any machine.
_OPENING_LOGO_NAME = "JNJ_Logo_SingleLine_Red_RGB.png"
# Closing ("Thank you") slide uses the single-line WHITE logo on its red background.
_CLOSING_LOGO_NAME = "JNJ_Logo_SingleLine_White_RGB.png"

# The three brand PNGs may live in a `jnj_logos` (or legacy `logo`) folder at the package root, one or
# two levels up (repo root), or the current working directory. Search all of these so the deck finds
# them regardless of how the project is laid out on a given machine.
_LOGO_SEARCH_DIRS = (
    _PACKAGE_ROOT / "jnj_logos",
    _PACKAGE_ROOT / "logo",
    _PACKAGE_ROOT.parent / "jnj_logos",
    _PACKAGE_ROOT.parent / "logo",
    _PACKAGE_ROOT.parent.parent / "jnj_logos",
    Path.cwd() / "jnj_logos",
)


def _find_logo(name: str) -> Path:
    """Return the first existing path for a logo filename across the known search dirs.

    Falls back to the package `logo/<name>` (which may not exist) so callers always get a Path; the
    renderer then draws the text fallback if the file is genuinely missing.
    """
    for directory in _LOGO_SEARCH_DIRS:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return _PACKAGE_ROOT / "logo" / name
_REFERENCES_DIR = _PACKAGE_ROOT / "references" / "market_overviews"
_BUSINESS_REFERENCES_DIR = _PACKAGE_ROOT / "references" / "business_overviews"
_CPR_REFERENCES_DIR = _PACKAGE_ROOT / "references" / "cpr_assessments"
_GACS_REFERENCES_DIR = _PACKAGE_ROOT / "references" / "gacs_assessments"
_OUTPUT_DIR = _PACKAGE_ROOT / "output"


@dataclass(frozen=True)
class ScopingDeckConfig:
    """Resolved configuration for a scoping-deck run."""

    settings: Settings
    entity_name: str
    logo_path: Path
    opening_logo_path: Path
    closing_logo_path: Path
    risk_scope_path: str          # AI Scope (risk-scope) workbook path or Delta table FQN; "" = skip 9/10
    risk_scope_sheet: str         # sheet holding the AI Scope rows (excel source)
    risk_scope_source: str        # "excel" | "db_table" — how risk_scope_path is read
    references_dir: Path
    business_references_dir: Path
    cpr_references_dir: Path
    gacs_references_dir: Path
    output_dir: Path
    past_review_source: str
    past_review_path: str
    past_review_excel_sheet: str
    # Output F (per-scope transaction totals) lives in the same main audit workbook as Output E.
    output_f_path: str = ""
    output_f_sheet: str = SHEET_F_NAME
    questionnaire_index_name: str = "questionnaire_index"
    past_review_index_name: str = "past_review_gacs_and_cpr_index"
    questionnaire_types: tuple[str, ...] = ("planning", "idr")
    retrieval_top_k: int = 6
    max_context_chunks: int = 24
    include_supporting_docs: bool = True
    supporting_docs_top_k: int = 6

    @property
    def full_questionnaire_index(self) -> str:
        """Fully qualified Unity Catalog name of the questionnaire vector index."""
        return self._qualify(self.questionnaire_index_name)

    @property
    def full_past_review_index(self) -> str:
        """Fully qualified Unity Catalog name of the past-review vector index."""
        return self._qualify(self.past_review_index_name)

    def _qualify(self, index_name: str) -> str:
        s = self.settings
        return f"{s.databricks_catalog}.{s.databricks_schema_name}.{index_name}"


class ConfigLoader:
    """Builds a ScopingDeckConfig from environment variables and the shared Settings."""

    @staticmethod
    def _env(name: str) -> str:
        return (os.getenv(name, "") or "").strip()

    @staticmethod
    def _sibling_or_search(base_logo: Path, name: str) -> Path:
        """Prefer the logo sitting beside the resolved shorthand logo; else search known dirs."""
        sibling = base_logo.with_name(name)
        return sibling if sibling.is_file() else _find_logo(name)

    @classmethod
    def load(cls) -> ScopingDeckConfig:
        settings = get_settings()
        entity_name = cls._env("SCOPING_DECK_ENTITY_NAME") or settings.auditor_id
        logo_path = Path(cls._env("SCOPING_DECK_LOGO_PATH") or _find_logo(_SHORTHAND_LOGO_NAME))
        # Opening/closing logos: honour an explicit env override, else look next to the resolved
        # shorthand logo (all three PNGs ship together), else fall back to the folder search.
        opening_logo_path = Path(
            cls._env("SCOPING_DECK_OPENING_LOGO_PATH")
            or cls._sibling_or_search(logo_path, _OPENING_LOGO_NAME)
        )
        closing_logo_path = Path(
            cls._env("SCOPING_DECK_CLOSING_LOGO_PATH")
            or cls._sibling_or_search(logo_path, _CLOSING_LOGO_NAME)
        )
        past_review_source = cls._env("SCOPING_DECK_PAST_REVIEW_SOURCE") or "excel"
        past_review_path = cls._env("SCOPING_DECK_PAST_REVIEW_PATH")
        # Output F: an explicit table/path wins. For the EXCEL source it defaults to the SAME workbook as
        # Output E (both are sheets in the main audit file); for db_table, E and F are SEPARATE tables, so
        # there is NO fallback to the Output E table — the explicit Output F table must be supplied.
        output_f_path = cls._env("SCOPING_DECK_OUTPUT_F_PATH") or (
            past_review_path if past_review_source.strip().lower() == "excel" else ""
        )
        return ScopingDeckConfig(
            settings=settings,
            entity_name=entity_name,
            logo_path=logo_path,
            opening_logo_path=opening_logo_path,
            closing_logo_path=closing_logo_path,
            risk_scope_path=cls._env("SCOPING_DECK_RISK_SCOPE_PATH"),
            risk_scope_sheet=cls._env("SCOPING_DECK_RISK_SCOPE_SHEET") or "AI Scope - Summary",
            risk_scope_source=cls._env("SCOPING_DECK_RISK_SCOPE_SOURCE") or "excel",
            references_dir=_REFERENCES_DIR,
            business_references_dir=_BUSINESS_REFERENCES_DIR,
            cpr_references_dir=_CPR_REFERENCES_DIR,
            gacs_references_dir=_GACS_REFERENCES_DIR,
            output_dir=_OUTPUT_DIR,
            past_review_source=past_review_source,
            past_review_path=past_review_path,
            past_review_excel_sheet=cls._env("SCOPING_DECK_PAST_REVIEW_SHEET") or SHEET_E_NAME,
            output_f_path=output_f_path,
            output_f_sheet=cls._env("SCOPING_DECK_OUTPUT_F_SHEET") or SHEET_F_NAME,
        )
