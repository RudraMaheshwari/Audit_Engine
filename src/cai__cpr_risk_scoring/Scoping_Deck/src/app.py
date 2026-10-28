"""Orchestrates a scoping-deck run: ingestion, Market Overview generation, and deck rendering."""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
from src.utils.logger import get_logger

from .audit_metadata import AuditMetadata, AuditMetadataProvider
from .business_overview.generator import BusinessOverviewGenerator
from .config import ScopingDeckConfig
from .cpi.cpi_block import CpiBlockComposer
from .cpi.cpi_repository import CpiRepository
from .deck.renderer import DeckRenderer
from .deck.spec import DECK_SPEC, SAM_1_DECK_SPEC, SECTION_BREAK
from .ingestion.questionnaire_index import QuestionnaireIndexManager
from .ingestion.supporting_docs_ingestor import SupportingDocsIngestor
from .market_overview.context_retriever import MarketContextRetriever
from .market_overview.generator import MarketOverviewGenerator
from .past_review.index_manager import PastReviewIndexManager
from .past_review.ingestor import PastReviewIngestor
from .past_review.models import ReviewKind
from .past_review.table_builder import PastReviewTable, PastReviewTableBuilder
from .risks_noted.generator import RisksNotedGenerator
from .scope_areas.generator import ScopeAreasBuilder
from .si_legal.generator import SiLegalSlideGenerator
from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider
from src.observability.simple import trace_run

logger = get_logger(__name__)

_MARKET_OVERVIEW_CONTENT_KEY = "market_overview"
_SAM_1_AUDITOR_ID = "Sam_1"
_SAM_1_QUESTIONNAIRE_VOLUME = "/Volumes/nbod_gaagd/cpr_risk_analysis/questionnaire_data/Sam_1/"


@dataclass
class ScopingDeckResult:
    """Outcome of a run: the generated Market Overview text and the saved deck path."""

    market_overview: str
    deck_path: Path


class ScopingDeckApp:
    """Runs the end-to-end scoping-deck workflow for the configured auditor."""

    def __init__(self, config: ScopingDeckConfig) -> None:
        self._config = config
        self._embeddings = AzureOpenAIEmbeddingProvider(config.settings).get_client()
        self._index = QuestionnaireIndexManager(config, self._embeddings)
        self._past_review_index = PastReviewIndexManager(config, self._embeddings)

    @property
    def _is_sam_1(self) -> bool:
        return self._config.settings.auditor_id == _SAM_1_AUDITOR_ID

    def run(self, skip_ingest: bool = False, force_ingest: bool = False, on_phase=None) -> ScopingDeckResult:
        """Ingest (unless skipped), generate slide content concurrently, render the deck."""
        _trace = trace_run(
            self._config.settings, engine="scoping_deck",
            auditor_id=self._config.settings.auditor_id,
        )

        def _phase(code: str) -> None:
            """Publish the current SD_* phase to the run status (no-op without a reporter)."""
            if on_phase is None:
                return
            try:
                on_phase(code)
            except Exception:  # noqa: BLE001 — status must never break deck generation
                pass
        self._ingest(skip_ingest, force_ingest)

        metadata = AuditMetadataProvider(self._config.settings).load()
        retriever = MarketContextRetriever(self._config, self._index)
        context_block = retriever.build_context_block()
        questionnaire_chunks = retriever.get_chunks()

        logger.info("=== Generating slide content concurrently (sam_1=%s) ===", self._is_sam_1)
        gen_mo = MarketOverviewGenerator(self._config, self._index)
        gen_bo = BusinessOverviewGenerator(self._config, self._index)

        with ThreadPoolExecutor(max_workers=10) as pool:
            if self._is_sam_1:
                future_overview_au = pool.submit(gen_mo.generate, context_block, "Australia")
                future_overview_nz = pool.submit(gen_mo.generate, context_block, "New Zealand")
                future_business_au = pool.submit(gen_bo.generate, context_block, "IM Australia")
                future_business_nz = pool.submit(gen_bo.generate, context_block, "IM New Zealand")
                future_cpi_nz = pool.submit(self._build_cpi_block_for, "NZL", "New Zealand",
                                            metadata.current_date.year)
            else:
                future_overview = pool.submit(gen_mo.generate, context_block)
                future_business = pool.submit(gen_bo.generate, context_block)
            future_risks = pool.submit(
                RisksNotedGenerator(self._config).generate, metadata.audit_entity
            )
            future_legal = pool.submit(
                SiLegalSlideGenerator(self._config, "legal").generate, metadata.audit_entity
            )
            future_si = pool.submit(
                SiLegalSlideGenerator(self._config, "si").generate, metadata.audit_entity
            )
            future_additional = pool.submit(
                SiLegalSlideGenerator(self._config, "additional").generate, metadata.audit_entity
            )
            future_past = pool.submit(self._build_past_review_tables)
            future_cpi = pool.submit(self._build_cpi_block, metadata)

            _phase("SD_MARKET_OVERVIEW")
            if self._is_sam_1:
                overview_au = future_overview_au.result()
                overview_nz = future_overview_nz.result()
                _phase("SD_BUSINESS_OVERVIEW")
                business_au = future_business_au.result()
                business_nz = future_business_nz.result()
                cpi_block_nz = future_cpi_nz.result()
                overview = overview_au
                business_sections = {**business_au, **business_nz}
            else:
                overview = future_overview.result()
                _phase("SD_BUSINESS_OVERVIEW")
                business_sections = future_business.result()
                cpi_block_nz = ""
            _phase("SD_SUMMARIZE_MGMT_RISKS")
            risks_noted = future_risks.result()
            _phase("SD_SUMMARIZE_LEGAL")
            legal_risks = self._group_psp_subbullets(future_legal.result())
            _phase("SD_SUMMARIZE_SI_HISTORY")
            si_summary = future_si.result()
            additional_input = future_additional.result()
            _phase("SD_SUMMARIZE_PRIOR_CPR")
            cpr_table, gacs_table = future_past.result()
            _phase("SD_SUMMARIZE_PRIOR_GACS")
            _phase("SD_FETCH_CPI")
            cpi_block = future_cpi.result()
            _phase("SD_UPDATE_CPI")

        # Apply sentence formatting to all bullet content.
        risks_noted    = self._fmt(risks_noted)
        legal_risks    = self._fmt(legal_risks)
        si_summary     = self._fmt(si_summary)
        additional_input = self._fmt(additional_input)
        business_sections = {k: self._fmt(v) for k, v in business_sections.items()}

        _phase("SD_UPDATE_SCOPE_DETAILS")
        scope_in_rows, scope_out_rows = ScopeAreasBuilder(self._config).build()
        _phase("SD_UPDATE_AREAS_NOT_IN_SCOPE")

        extra_content = {}
        if self._is_sam_1:
            extra_content["market_overview_au"] = self._combine_market_overview(
                self._fmt(overview_au), cpi_block
            )
            extra_content["market_overview_nz"] = self._combine_market_overview(
                self._fmt(overview_nz), cpi_block_nz
            )

        deck_path = self._render_deck(
            metadata, overview, business_sections, risks_noted, legal_risks, si_summary,
            additional_input, cpi_block, cpr_table, gacs_table, scope_in_rows, scope_out_rows,
            extra_content=extra_content,
            questionnaire_chunks=questionnaire_chunks,
        )
        _trace.finish()
        return ScopingDeckResult(market_overview=overview, deck_path=deck_path)

    def _ingest(self, skip_ingest: bool, force_ingest: bool) -> None:
        if skip_ingest:
            self._index.ensure_index()
            self._past_review_index.ensure_index()
            logger.info("=== slide_1 skipped (--skip-ingest) ===")
            return

        logger.info("=== slide_1: questionnaire index -> %s ===", self._config.full_questionnaire_index)
        self._index.ensure_index()

        # Risk Analysis (and the standalone ingest_questionnaire_index.py script) are the writers of
        # the shared questionnaire index; the deck only READS it (market/business overview retrieval).
        # The deck NEVER ingests here — ingesting duplicated every Q&A under a second chunk_id and
        # bloated the shared endpoint toward its capacity limit. If the index has no rows for this
        # auditor yet, the deck logs it and continues (overviews fall back to whatever context exists)
        # rather than attempting an upsert that would fail on a full endpoint and kill the whole run.
        auditor_id = self._config.settings.auditor_id
        try:
            populated = self._index.is_populated(auditor_id=auditor_id)
        except Exception as exc:  # noqa: BLE001
            populated = False
            logger.warning("[Ingest] questionnaire is_populated probe failed — continuing | %s", exc)
        if populated:
            logger.info("[Ingest] questionnaire index populated — reusing | auditor=%s", auditor_id)
        else:
            logger.warning(
                "[Ingest] questionnaire index has NO rows for auditor=%s — the deck does NOT ingest. "
                "Populate it first via Risk Analysis or "
                "`ingest_questionnaire_index.py --auditor %s ...`. Continuing with reduced context.",
                auditor_id, auditor_id,
            )

        SupportingDocsIngestor(self._config).ensure()
        PastReviewIngestor(self._config, self._past_review_index).ingest(force=force_ingest)
        logger.info("=== slide_1 complete | questionnaire reused (deck does not ingest) ===")

    def _build_past_review_tables(self) -> tuple[PastReviewTable, PastReviewTable]:
        empty = PastReviewTable(rows=(), year=None)
        try:
            llm = AzureOpenAILLMProvider(self._config.settings).get_client()
            builder = PastReviewTableBuilder(self._config, self._past_review_index, llm)
            return builder.build(ReviewKind.CPR), builder.build(ReviewKind.GACS)
        except Exception as exc:
            logger.warning("[PastReview] Table build failed — CPR/GACS slides omitted | %s", exc)
            return empty, empty

    def _render_deck(
        self,
        metadata: AuditMetadata,
        overview: str,
        business_sections: dict[str, str],
        risks_noted: str,
        legal_risks: str,
        si_summary: str,
        additional_input: str,
        cpi_block: str,
        cpr_table: PastReviewTable,
        gacs_table: PastReviewTable,
        scope_in_rows: list | None = None,
        scope_out_rows: list | None = None,
        extra_content: dict | None = None,
        questionnaire_chunks: list | None = None,
    ) -> Path:
        content = {
            _MARKET_OVERVIEW_CONTENT_KEY: self._combine_market_overview(overview, cpi_block),
            "risks_noted": risks_noted,
            "legal_risks": legal_risks,
            "si_summary": si_summary,
            "additional_input": additional_input,
            "cpr_findings": list(cpr_table.rows),
            "gacs_findings": list(gacs_table.rows),
            "scope_in_rows": scope_in_rows or [],
            "scope_out_rows": scope_out_rows or [],
            **business_sections,
            **(extra_content or {}),
        }
        context = {
            "entity": self._config.entity_name,
            "cpr_year": str(cpr_table.year or ""),
            "gacs_year": str(gacs_table.year or ""),
            **metadata.as_context(),
        }
        spec = SAM_1_DECK_SPEC if self._is_sam_1 else DECK_SPEC
        renderer = DeckRenderer(spec=spec)
        deck_path = self._resolve_deck_path(metadata)
        renderer.render(
            content, deck_path, context,
            logo_path=str(self._config.logo_path),
            opening_logo_path=str(self._config.opening_logo_path),
            closing_logo_path=str(self._config.closing_logo_path),
        )
        self._export_reference_excel(content, metadata, cpr_table, gacs_table, deck_path, questionnaire_chunks or [])
        return deck_path

    @staticmethod
    def _combine_market_overview(overview: str, cpi_block: str) -> str:
        """Merge the Market Overview bullets and the CPI block into one flowing region.

        Each overview line is normalised to a bullet marker; the CPI block (bold heading, intro, and
        its own bullets) follows immediately, so there is no fixed gap between them.
        """
        bullets = []
        for line in (overview or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            bullets.append(stripped if stripped.startswith(("- ", "• ", "* ")) else f"- {stripped}")
        combined = "\n".join(bullets)
        if cpi_block.strip():
            if combined:
                combined = f"{combined}\n{SECTION_BREAK}\n{cpi_block}"
            else:
                combined = cpi_block
        return combined

    def _build_cpi_block(self, metadata: AuditMetadata) -> str:
        return self._build_cpi_block_for(metadata.iso3, metadata.country, metadata.current_date.year)

    def _build_cpi_block_for(self, iso3: str, country: str, year: int) -> str:
        logger.info("=== CPI lookup for %s (%s) ===", country, iso3)
        try:
            result = CpiRepository().fetch(iso3, country, year)
        except Exception as exc:
            logger.warning("[CPI] Lookup failed for %s — CPI block omitted | %s", country, exc)
            return ""
        if result is None:
            return ""
        return CpiBlockComposer().compose(result)

    @staticmethod
    def _group_psp_subbullets(text: str) -> str:
        """Detect consecutive bullets all tagged '(PSP)' and make all-but-first into sub-bullets.

        Any run of 2+ consecutive top-level '- ' bullets whose text contains '(PSP)' (case-
        insensitive) is restructured: the first keeps its '- ' marker; all subsequent ones in the
        same run become '  - ' (two-space indent) sub-bullets rendered under it.
        """
        if not text:
            return text
        lines = text.splitlines()
        result: list[str] = []
        psp_run: list[str] = []

        def _flush_run() -> None:
            if not psp_run:
                return
            for i, ln in enumerate(psp_run):
                result.append(ln if i == 0 else "  " + ln.lstrip())
            psp_run.clear()

        for line in lines:
            stripped = line.strip()
            is_top_bullet = stripped.startswith(("- ", "* ", "• "))
            is_psp = "(psp)" in stripped.lower()
            if is_top_bullet and is_psp:
                psp_run.append(line)
            else:
                _flush_run()
                result.append(line)
        _flush_run()
        return "\n".join(result)

    @staticmethod
    def _fmt(text: str) -> str:
        """Ensure every bullet line starts with a capital letter and ends with a full stop.

        Also capitalises the first letter after ': ' and ' – '/' — '.
        Applies to lines that begin with a bullet marker ('- ', '• ', '  - ', etc.).
        """
        if not text:
            return text

        _BULLET_PREFIXES = ("- ", "* ", "• ", "  - ", "  * ", "  • ")
        _NO_PERIOD_ENDINGS = (".", "?", "!", ":", "…")
        _CAP_COLON = re.compile(r'(:\s+)([a-z])')
        _CAP_DASH  = re.compile(r'(\s+[–—]\s+)([a-z])')

        def _cap(m):
            return m.group(1) + m.group(2).upper()

        result = []
        for line in text.splitlines():
            stripped = line.lstrip()
            if not any(stripped.startswith(p) for p in _BULLET_PREFIXES):
                result.append(line)
                continue
            for p in _BULLET_PREFIXES:
                if stripped.startswith(p):
                    leading = line[: len(line) - len(stripped)]
                    content = stripped[len(p):].strip()
                    if not content:
                        result.append(line)
                        break
                    content = content[0].upper() + content[1:]
                    content = _CAP_COLON.sub(_cap, content)
                    content = _CAP_DASH.sub(_cap, content)
                    if not content.endswith(_NO_PERIOD_ENDINGS):
                        content += "."
                    result.append(f"{leading}{p}{content}")
                    break
            else:
                result.append(line)
        return "\n".join(result)

    def _export_reference_excel(
        self,
        content: dict,
        metadata: AuditMetadata,
        cpr_table: PastReviewTable,
        gacs_table: PastReviewTable,
        deck_path: Path,
        questionnaire_chunks: list | None = None,
    ) -> None:
        try:
            from .deck.excel_exporter import ReferenceExcelExporter
            excel_path = deck_path.with_suffix(".xlsx")
            ReferenceExcelExporter().export(
                content=content,
                metadata=metadata,
                cpr_table=cpr_table,
                gacs_table=gacs_table,
                output_path=excel_path,
                questionnaire_chunks=questionnaire_chunks or [],
            )
            logger.info("[Excel] Reference workbook -> %s", excel_path)
        except Exception as exc:
            logger.warning("[Excel] Export failed — skipped | %s", exc)

    def _resolve_deck_path(self, metadata: AuditMetadata) -> Path:
        override = (os.getenv("SCOPING_DECK_SLIDE_PATH", "") or "").strip()
        if override:
            return Path(override)
        # Filename nomenclature: CPR_<Audit Entity>_<Scope Start> to <Scope End>_Scoping Deck
        start = f"{metadata.scope_period_start:%d %b %Y}"
        end = f"{metadata.scope_period_end:%d %b %Y}"
        name = f"CPR_{metadata.audit_entity}_{start} to {end}_Scoping Deck"
        return self._config.output_dir / f"{self._sanitize_filename(name)}.pptx"

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Drop characters not allowed in Windows filenames and collapse repeated whitespace."""
        cleaned = re.sub(r'[\\/:*?"<>|]', "", name)
        return re.sub(r"\s+", " ", cleaned).strip()
