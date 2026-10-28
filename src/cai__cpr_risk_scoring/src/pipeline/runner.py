import asyncio
import hashlib
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.parent


class _DropPatNotice:
    def __init__(self, stream):
        self._stream = stream
        self._drop_newline = False

    def write(self, text):
        if "Using a Personal Authentication Token" in text:
            self._drop_newline = True
            return
        if self._drop_newline and text == "\n":
            self._drop_newline = False
            return
        self._drop_newline = False
        self._stream.write(text)

    def flush(self):
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


sys.stdout = _DropPatNotice(sys.stdout)
sys.stderr = _DropPatNotice(sys.stderr)

from src.agents.hcbi_policy_agent import HcbiPolicyAgent
from src.agents.policy_knowledge_agent import PolicyKnowledgeAgent
from src.agents.risk_classifier_agent import RiskClassifierAgent
from src.agents.risk_evaluation_agent import RiskEvaluationAgent
from src.agents.risk_writer_agent import RiskWriterAgent
from src.agents.scope_identifier_agent import ScopeIdentifierAgent
from src.agents.supporting_docs_agent import SupportingDocsAgent
from src.agents.transactional_data_agent import TransactionalDataAgent
from src.agents.transactional_data_analysis_agent import TransactionalDataAnalysisAgent
from src.config.constants import (
    FOREIGN_OFFICIAL_IMPACT_FLOOR,
    HCBI_SCOPE_ID,
    MATRIX_BAND_HIGH_MIN,
    MATRIX_BAND_MEDIUM_MIN,
    MISSING_EXPECTED_EVIDENCE_LIKELIHOOD_FLOOR,
    AGENT_PAST_AUDIT_SUMMARY,
    AGENT_RISK_AGGREGATION,
    AGENT_TRANSACTIONAL_ANALYSIS,
    OBS_INGEST_COMPLETED,
    OBS_INGEST_DOWNLOAD_FAILED,
    OBS_INGEST_EXTRACT_FAILED,
    OBS_INGEST_NO_CHUNKS,
    OBS_INGEST_UPSERT_FAILED,
    OBS_QSTATUS_COMPLETED,
    OBS_QSTATUS_ERROR,
    OBS_STAGE_AGGREGATION,
    OBS_STAGE_PAST_AUDIT,
    OBS_STAGE_PER_QUESTION,
    OBS_STAGE_TRANSACTIONAL_ANALYSIS,
    OBS_STATUS_COMPLETED,
    OBS_STATUS_PARTIAL,
    QUESTION_CONCURRENCY,
    RECURRENCE_LIKELIHOOD_INCREMENT,
)
from src.config.databricks_auth import bearer_token
from src.config.settings import get_settings
from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider
from src.ingestion.chunker import Chunker
from src.ingestion.extractors.ocr.azure_document_intelligence_provider import AzureDocumentIntelligenceOCRProvider
from src.ingestion.extractors.ocr.tesseract_provider import TesseractOCRProvider
from src.ingestion.loader import DocumentLoader
from src.ingestion.volume_downloader import DatabricksVolumeDownloader, download_volume_file
from src.services.translation import TranslationService
from src.models.schemas import AuditState, DocumentSource
from src.output.output_writer import OutputWriter
from src.observability import (
    ObsContext,
    ObservabilityRecorder,
    ObservabilityTracer,
    observe,
    per_question_default,
)
from src.observability.prompt_registry import (
    get_prompt_versions,
    log_prompt_versions,
    prompt_bundle_id,
)
from src.agents.past_audit_summarizer_agent import PastAuditSummarizerAgent
from src.agents.risk_aggregation_agent import RiskAggregationAgent
from src.knowledge_base import KnowledgeRetriever, build_raw_table_chunks, build_transactional_chunks
from src.pipeline.aggregation_stage import run_aggregation
from src.pipeline.continuity import compute_continuity, write_continuity_report
from src.pipeline.past_audit_stage import summarize_past_audits
from src.pipeline.graph import build_audit_graph
from src.pipeline.prior_context import format_prior_context
from src.pipeline.transactional_context import format_transactional_context
from src.services.tpi_inventory import (
    format_tpi_inventory_block,
    load_tpi_inventory_names,
    resolve_auditor_sheet,
)
from src.repositories.output_repo import OutputRepository
from src.repositories.questionnaire_repo import DatabricksQuestionnaireRepository
from src.schemas.state import DocumentType, RiskOutput
from src.services.conflict_detector import ConflictDetector
from src.ingestion.extractors.cpr_scope_extractor import CPRScopeExtractor
from src.tools.evidence_retrieval_tool import EvidenceRetrievalTool
from src.tools.hcbi_retrieval_tool import HcbiRetrievalTool
from src.tools.policy_retrieval_tool import PolicyRetrievalTool
from src.tools.scope_policy_retrieval_tool import ScopePolicyRetrievalTool
from src.utils.logger import get_logger
from src.vector_store.index_manager import VectorIndexManager

logger = get_logger(__name__)

_INDEX_PIPELINE = [
    ("hcbi",   DocumentType.GLOBAL_POLICY, DocumentSource.HCBI,   "hcbi_volume_path"),
    ("policy", DocumentType.TPI_POLICY,    DocumentSource.POLICY,  "policy_volume_path"),
]

_INGEST_FILE_WORKERS = 4  # parallel files processed simultaneously
_KB_CONTEXT_TOP_K = 25


def _hash_text(text: str | None) -> str | None:
    """Stable sha256 of question text — lets us key a question without storing raw content."""
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _as_int(value, fallback: int) -> int:
    """Parse a question id to int, falling back to the loop index when it is not numeric."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def _build_ocr_provider(settings) -> TesseractOCRProvider | AzureDocumentIntelligenceOCRProvider:
    if settings.azure_document_intelligence_endpoint and settings.azure_document_intelligence_key:
        logger.info("[Ingest] OCR provider: Azure Document Intelligence")
        return AzureDocumentIntelligenceOCRProvider(
            endpoint=settings.azure_document_intelligence_endpoint,
            api_key=settings.azure_document_intelligence_key,
        )
    logger.warning("[Ingest] Azure DI credentials not set — falling back to Tesseract OCR")
    return TesseractOCRProvider(lang="eng+jpn", tesseract_cmd=settings.tesseract_cmd)


# Set-once (before the ingest thread pool starts) so the parallel ingest workers can write an
# obs_ingestion row per file. Read-only during ingestion; the recorder itself is thread-safe.
# A no-op when observability is disabled (recorder stays None).
_INGEST_OBS: dict = {"recorder": None, "run_id": None, "embedding_model": "", "embedding_dimensions": 0}


def _process_and_upsert_file(
    *,
    file_info: dict,
    base_volume_path: str,
    index_key: str,
    doc_type: DocumentType,
    doc_source: DocumentSource,
    scope_id: str,
    auditor_folder: str | None,
    loader: DocumentLoader,
    chunker: Chunker,
    index_manager,
    downloader: DatabricksVolumeDownloader,
    tmp_dir: Path,
    failures: list | None = None,
    change_detect: bool = False,
) -> int:
    """Download → extract → chunk → upsert a single file. Returns chunk count.

    Also writes one obs_ingestion row (status + chunk/token/embedding counts + timing) so the
    ingestion/embedding layer is observable. The recorder write never raises into ingestion.
    """
    file_path = file_info["path"]
    file_name = file_info["name"]
    folder_label = auditor_folder or "root"
    started = datetime.now(timezone.utc)

    def _rec(status: str, num_chunks: int = 0, total_tokens: int = 0, error: str | None = None) -> None:
        recorder = _INGEST_OBS.get("recorder")
        if recorder is None:
            return
        try:
            recorder.record_ingestion(
                run_id=_INGEST_OBS.get("run_id"),
                index_key=index_key,
                document_type=getattr(doc_type, "value", str(doc_type)),
                document_source=getattr(doc_source, "value", str(doc_source)),
                source_file=file_name,
                folder=folder_label,
                scope_id=scope_id,
                status=status,
                num_chunks=num_chunks,
                total_tokens=total_tokens,
                embedding_model=_INGEST_OBS.get("embedding_model"),
                embedding_dimensions=_INGEST_OBS.get("embedding_dimensions"),
                error=error,
                started_at=started,
            )
        except Exception:  # observability must never break ingestion
            pass

    # Compute relative path to preserve subfolder structure
    rel = file_path[len(base_volume_path.rstrip("/")):].lstrip("/")
    local_path = tmp_dir / rel

    logger.info("[Ingest] ▶ Downloading | folder=%s | file=%s", folder_label, file_name)
    ok = downloader.download_single_file(file_path, local_path)
    if not ok:
        logger.warning("[Ingest] Download failed — skipping | folder=%s | file=%s", folder_label, file_name)
        _rec(OBS_INGEST_DOWNLOAD_FAILED, error="download failed")
        if failures is not None:
            failures.append(file_name)   # surfaced in the end-of-ingestion summary
        return 0

    # Change detection (KB indexes): fingerprint the downloaded bytes and compare to what's indexed.
    #   unchanged  → skip (no extract/embed/upsert),
    #   changed    → delete this file's stale chunks, then re-ingest below,
    #   new / "" → ingest below. The hash is stamped onto every chunk so the next run can compare.
    content_hash = ""
    if change_detect:
        try:
            content_hash = hashlib.sha256(local_path.read_bytes()).hexdigest()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Ingest] Could not hash file — ingesting unconditionally | file=%s | %s",
                           file_name, exc)
        if content_hash:
            stored = index_manager.fetch_document_hash(index_key, auditor_folder or "", file_name)
            if stored == content_hash:
                logger.info("[Ingest] ⏭ Unchanged — skipping | folder=%s | file=%s", folder_label, file_name)
                local_path.unlink(missing_ok=True)
                return 0
            if stored:
                logger.info("[Ingest] ✎ Changed — re-ingesting | folder=%s | file=%s", folder_label, file_name)
                try:
                    index_manager.delete_by_document_name(
                        index_key, file_name, auditor_folder=auditor_folder or None,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[Ingest] Stale-chunk delete failed (continuing) | file=%s | %s",
                                   file_name, exc)

    logger.info("[Ingest] ⚙ Extracting  | folder=%s | file=%s", folder_label, file_name)
    try:
        doc = loader.load_file(local_path, scope_id=scope_id, document_type=doc_type)
    except Exception as exc:
        logger.warning("[Ingest] Extraction failed | folder=%s | file=%s | error=%s", folder_label, file_name, exc)
        _rec(OBS_INGEST_EXTRACT_FAILED, error=str(exc))
        return 0
    finally:
        try:
            local_path.unlink(missing_ok=True)
        except Exception:
            pass

    chunks = chunker.chunk(doc, doc_source)
    if not chunks:
        logger.warning("[Ingest] No chunks produced | folder=%s | file=%s", folder_label, file_name)
        _rec(OBS_INGEST_NO_CHUNKS)
        return 0

    # Tag with auditor folder (if applicable) and stamp the content fingerprint onto each chunk so a
    # later run can detect an in-place edit to this same file.
    if auditor_folder or content_hash:
        tagged = []
        for c in chunks:
            upd: dict = {}
            if auditor_folder:
                upd["auditor_folder"] = auditor_folder
            if content_hash:
                upd["metadata"] = {**(c.metadata or {}), "content_hash": content_hash}
            tagged.append(c.model_copy(update=upd) if upd else c)
        chunks = tagged

    total_tokens = sum(int(getattr(c, "token_count", 0) or 0) for c in chunks)

    logger.info("[Ingest] ↑ Upserting   | folder=%s | file=%s | chunks=%d", folder_label, file_name, len(chunks))
    try:
        index_manager.upsert_chunks(index_key, chunks)
    except Exception as exc:
        logger.warning("[Ingest] Upsert failed | folder=%s | file=%s | error=%s", folder_label, file_name, exc)
        _rec(OBS_INGEST_UPSERT_FAILED, num_chunks=len(chunks), total_tokens=total_tokens, error=str(exc))
        return 0

    logger.info("[Ingest] ✔ Done        | folder=%s | file=%s | chunks=%d", folder_label, file_name, len(chunks))
    _rec(OBS_INGEST_COMPLETED, num_chunks=len(chunks), total_tokens=total_tokens)
    return len(chunks)


def _ensure_indexes(index_manager, embedding_provider, settings) -> None:
    ocr_provider = _build_ocr_provider(settings)
    loader  = DocumentLoader(ocr_provider, translator=TranslationService(settings), settings=settings)
    chunker = Chunker(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)

    for index_key, doc_type, doc_source, volume_attr in _INDEX_PIPELINE:
        scope_id = HCBI_SCOPE_ID if index_key == "hcbi" else settings.current_scope_id

        # Retrieval on these reference indexes is unfiltered, so "already populated" is scope-
        # independent — this keeps a run-scope rename from forcing a re-ingest.
        if index_manager.is_populated_any(index_key):
            logger.info("[Ingest] Index already populated — skipping | index=%s", index_key)
            continue

        index_manager.delete_index(index_key)
        index_manager.ensure_index(index_key)

        volume_path = getattr(settings, volume_attr)
        logger.info("[Ingest] Listing files from Volume | index=%s | volume=%s", index_key, volume_path)

        downloader = DatabricksVolumeDownloader(settings.databricks_host, bearer_token(settings))
        files = downloader.list_files(volume_path)

        if not files:
            logger.warning("[Ingest] No supported files found | index=%s | volume=%s", index_key, volume_path)
            continue

        logger.info("[Ingest] Found %d files | index=%s | starting parallel ingest (workers=%d)",
                    len(files), index_key, _INGEST_FILE_WORKERS)

        tmp_dir = Path(tempfile.mkdtemp(prefix=f"audit_{index_key}_"))
        total_chunks = 0
        completed = 0

        try:
            with ThreadPoolExecutor(max_workers=_INGEST_FILE_WORKERS) as pool:
                futures = {
                    pool.submit(
                        _process_and_upsert_file,
                        file_info=f,
                        base_volume_path=volume_path,
                        index_key=index_key,
                        doc_type=doc_type,
                        doc_source=doc_source,
                        scope_id=scope_id,
                        auditor_folder=None,
                        loader=loader,
                        chunker=chunker,
                        index_manager=index_manager,
                        downloader=downloader,
                        tmp_dir=tmp_dir,
                    ): f["name"]
                    for f in files
                }
                for fut in as_completed(futures):
                    fname = futures[fut]
                    try:
                        n = fut.result(timeout=300)
                        total_chunks += n
                    except Exception as exc:
                        logger.warning("[Ingest] Worker error | file=%s | error=%s", fname, exc)
                    completed += 1
                    logger.info("[Ingest] Progress | index=%s | %d/%d files | chunks_so_far=%d",
                                index_key, completed, len(files), total_chunks)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        logger.info("[Ingest] Done | index=%s | files=%d | total_chunks=%d", index_key, len(files), total_chunks)
        index_manager.wait_until_ready(index_key)


def _ensure_supporting_docs_index(index_manager, settings, auditor_id: str) -> None:
    """Ingest the current auditor's supporting documents, with a per-auditor pre-check.

    The supporting-docs index is shared across auditors (partitioned by auditor_folder). If this
    auditor's documents are already embedded, ingestion is skipped so nothing is re-embedded. Only
    the current auditor's subfolder is ingested.
    """
    # Re-ingest on change (same as the KB indexes): when the auditor already has data, each file's
    # content hash is compared and only NEW or EDITED files are re-embedded (unchanged files skipped).
    # A fresh/empty index is a full ingest. This replaces the old all-or-nothing skip, which missed
    # files added or changed after the first ingest.
    change_detect = index_manager.is_populated_for_auditor("supporting", auditor_id)
    index_manager.ensure_index("supporting")
    if change_detect:
        logger.info(
            "[Ingest] supporting_docs already populated — re-ingesting only changed/new files | auditor=%s",
            auditor_id,
        )

    auditor_volume = settings.resolved_supporting_docs_folder
    logger.info("[Ingest] Listing auditor supporting docs | volume=%s", auditor_volume)

    downloader = DatabricksVolumeDownloader(settings.databricks_host, bearer_token(settings))
    all_files = downloader.list_files(auditor_volume)
    if not all_files:
        logger.warning("[Ingest] No supporting docs found for auditor | volume=%s", auditor_volume)
        return

    ocr_provider = _build_ocr_provider(settings)
    loader  = DocumentLoader(ocr_provider, translator=TranslationService(settings), settings=settings)
    chunker = Chunker(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)

    logger.info("[Ingest] Supporting docs | auditor=%s | files=%d | workers=%d | starting ingest",
                auditor_id, len(all_files), _INGEST_FILE_WORKERS)

    tmp_dir = Path(tempfile.mkdtemp(prefix="audit_supporting_"))
    total_chunks = 0
    completed = 0
    download_failures: list[str] = []   # files that could not be fetched (raw + SDK) — reported at end

    try:
        with ThreadPoolExecutor(max_workers=_INGEST_FILE_WORKERS) as pool:
            futures = {
                pool.submit(
                    _process_and_upsert_file,
                    file_info=f,
                    base_volume_path=auditor_volume,
                    index_key="supporting",
                    doc_type=DocumentType.SUPPORTING_DOC,
                    doc_source=DocumentSource.SUPPORTING,
                    scope_id="supporting",
                    auditor_folder=auditor_id,
                    loader=loader,
                    chunker=chunker,
                    index_manager=index_manager,
                    downloader=downloader,
                    tmp_dir=tmp_dir,
                    failures=download_failures,
                    change_detect=change_detect,
                ): f["name"]
                for f in all_files
            }
            for fut in as_completed(futures):
                fname = futures[fut]
                try:
                    n = fut.result(timeout=300)
                    total_chunks += n
                except Exception as exc:
                    logger.warning("[Ingest] Worker error | file=%s | error=%s", fname, exc)
                completed += 1
                logger.info("[Ingest] Progress | supporting | %d/%d files | chunks_so_far=%d",
                            completed, len(all_files), total_chunks)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if download_failures:
        logger.error(
            "[Ingest] ⚠ %d/%d supporting file(s) could NOT be downloaded (raw + SDK) and were NOT "
            "ingested | auditor=%s | files=%s",
            len(download_failures), len(all_files), auditor_id, download_failures,
        )
    logger.info("[Ingest] Done | supporting | auditor=%s | files=%d | ingested=%d | download_failed=%d | total_chunks=%d",
                auditor_id, len(all_files), len(all_files) - len(download_failures),
                len(download_failures), total_chunks)
    index_manager.wait_until_ready("supporting")


def _ensure_scope_policy_index(index_manager, settings) -> None:
    if index_manager.is_populated_any("scope_policy"):
        logger.info("[Ingest] scope_policy index already populated — skipping")
        return

    index_manager.delete_index("scope_policy")
    index_manager.ensure_index("scope_policy")

    cache_dir = _PROJECT_ROOT / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    local_cpr = cache_dir / "CPR AI - System Guide.xlsx"

    logger.info(
        "[Ingest] Downloading CPR scope area policy file | path=%s",
        settings.scope_area_policy_file_path,
    )
    ok = download_volume_file(
        settings.databricks_host,
        bearer_token(settings),
        settings.scope_area_policy_file_path,
        local_cpr,
    )
    if not ok:
        logger.warning("[Ingest] CPR scope policy file download failed — index not populated")
        return

    chunks = CPRScopeExtractor().extract(local_cpr)
    if not chunks:
        logger.warning("[Ingest] CPR extractor returned no chunks — index not populated")
        return

    logger.info("[Ingest] CPR extracted | chunks=%d", len(chunks))
    total = len(chunks)
    batches = [chunks[i : i + 50] for i in range(0, total, 50)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(index_manager.upsert_chunks, "scope_policy", b): b for b in batches}
        for fut in as_completed(futures):
            fut.result(timeout=120)

    logger.info("[Ingest] scope_policy index populated | chunks=%d", total)
    index_manager.wait_until_ready("scope_policy")


def _download_or_cache(settings, volume_path: str, local_path) -> bool:
    """Reuse a previously downloaded reference file if present; otherwise fetch it from the Volume.

    Skips the download when a non-empty local copy already exists, unless refresh_reference_files
    (REFRESH_REFERENCE_FILES) forces a re-download.
    """
    local_path = Path(local_path)
    if (not settings.refresh_reference_files
            and local_path.exists() and local_path.stat().st_size > 0):
        logger.info("[Ref] Using cached copy (skip download) | name=%s", local_path.name)
        return True
    return download_volume_file(
        settings.databricks_host, bearer_token(settings), volume_path, local_path
    )


# File-name keywords that identify the TPI inventory list among the auditor's supporting docs.
# Per TC the file is named SD_<Engagement_no>_IDR_<Section_ID>_Q#_<Filename>; the <Filename> part
# carries the real name (e.g. "...TPI Inventory List.xlsx"), which contains one of these keywords.
_TPI_INVENTORY_KEYWORDS = ("tpi", "inventory")
_TPI_INVENTORY_EXTS = {".xlsx", ".xls"}


def _resolve_tpi_inventory_path(settings, auditor_id: str, cache_dir: Path) -> str | None:
    """Resolve the TPI inventory file to a local path, in priority order:
      1) EXPLICIT path provided via the API / env (settings.tpi_inventory_file_path) — authoritative,
         so a caller that names the exact Master list always wins over a fuzzy keyword match.
      2) Else auto-discover it in the auditor's SUPPORTING DOCS folder by filename keyword.
      3) Else return None — the caller SKIPS TPI matching gracefully (no inventory loaded).
    Best-effort: any failure in step 1 falls through to step 2, and any failure there to None."""
    # 1) Explicit path (API / env) wins — the user named the exact authoritative file.
    configured = (settings.tpi_inventory_file_path or "").strip()
    if configured:
        local_path = cache_dir / Path(configured).name
        if _download_or_cache(settings, configured, local_path):
            logger.info("[Ref] TPI inventory from configured/API path | path=%s", configured)
            return str(local_path)
        logger.warning("[Ref] TPI inventory configured path download failed — trying discovery | path=%s", configured)

    # 2) Discover in the auditor's supporting-docs folder (used when no explicit path was given).
    try:
        folder = (settings.resolved_supporting_docs_folder or "").rstrip("/")
        if folder:
            downloader = DatabricksVolumeDownloader(settings.databricks_host, bearer_token(settings))
            files = downloader.list_files(folder) or []
            candidates = [
                f for f in files
                if Path(f.get("name", "")).suffix.lower() in _TPI_INVENTORY_EXTS
                and any(k in f.get("name", "").lower() for k in _TPI_INVENTORY_KEYWORDS)
            ]
            # Prefer files that also follow the SD_<eng>_ prefix for the engagement being run.
            slug = (settings.engagement_slug or "").lower()
            candidates.sort(key=lambda f: (
                0 if f.get("name", "").lower().startswith("sd_") and (not slug or slug in f.get("name", "").lower())
                else 1,
                f.get("name", "").lower(),
            ))
            if candidates:
                chosen = candidates[0]
                local_path = cache_dir / Path(chosen["name"]).name
                if (local_path.exists() and local_path.stat().st_size > 0) or \
                        downloader.download_single_file(chosen["path"], local_path):
                    logger.info("[Ref] TPI inventory discovered in supporting docs | name=%s", chosen["name"])
                    return str(local_path)
                logger.warning("[Ref] TPI inventory '%s' found but could not be downloaded", chosen["name"])
            else:
                logger.info("[Ref] No TPI inventory file found in supporting docs | folder=%s", folder)
    except Exception as exc:  # noqa: BLE001 — discovery is best-effort
        logger.warning("[Ref] TPI inventory discovery in supporting docs failed | %s", exc)

    # 3) Nothing available — skip TPI matching.
    logger.warning("[Ref] TPI inventory not found via API path or supporting docs — TPI matching will be skipped")
    return None


def _prepare_reference_files(settings, auditor_id: str, llm=None) -> str:
    """Download the GL map + resolve the TPI inventory from the Volume to local cache, repoint settings
    to the local copies (so Phase 3 and the classifier injection can read them), resolve the auditor's
    inventory sheet, and return the formatted TPI inventory block (empty string if unavailable)."""
    cache_dir = _PROJECT_ROOT / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    gl_local  = cache_dir / Path(settings.hcc_gl_account_map_path).name
    if _download_or_cache(settings, settings.hcc_gl_account_map_path, gl_local):
        settings.hcc_gl_account_map_path = str(gl_local)
    else:
        logger.warning("[Ref] GL account map download failed — GL checks may be limited")

    # Resolve the TPI inventory: supporting docs (by TC naming) -> API path -> skip.
    tpi_path = _resolve_tpi_inventory_path(settings, auditor_id, cache_dir)
    if not tpi_path:
        settings.tpi_inventory_file_path = ""
        return ""
    settings.tpi_inventory_file_path = tpi_path

    # Auto-derive the auditor's sheet unless an explicit override is configured.
    if not settings.tpi_inventory_sheet:
        resolved = resolve_auditor_sheet(settings.tpi_inventory_file_path, auditor_id)
        if resolved:
            settings.tpi_inventory_sheet = resolved
            logger.info("[Ref] TPI inventory sheet for auditor '%s' -> %s", auditor_id, resolved)
        else:
            logger.warning(
                "[Ref] Could not resolve TPI inventory sheet for auditor '%s' — TPI matching skipped",
                auditor_id,
            )

    names = load_tpi_inventory_names(settings, llm=llm)
    logger.info(
        "[Ref] TPI inventory loaded | names=%d | sheet=%s",
        len(names), settings.tpi_inventory_sheet or "(unresolved)",
    )
    return format_tpi_inventory_block(names, settings.tpi_inventory_sheet)


def _ensure_kb_volume_index(
    index_manager,
    settings,
    *,
    index_key: str,
    auditor_volume: str,
    auditor_id: str,
    doc_source: DocumentSource,
) -> None:
    """Ingest a per-auditor knowledge-base volume (past-audit / SI-Legal / local-policies).

    `auditor_volume` is the resolved folder for this auditor (explicit API path, or
    <base volume>/<auditor_id>). The index is created only when the auditor has files to ingest.

    Re-ingest on change: when the auditor already has data, each file's content hash is compared to
    what's indexed and only NEW or EDITED files are re-embedded (unchanged files are skipped). A
    brand-new / empty index is a full ingest with no per-file comparison.
    """
    auditor_volume = auditor_volume.rstrip("/")
    downloader = DatabricksVolumeDownloader(settings.databricks_host, bearer_token(settings))
    files = downloader.list_files(auditor_volume)
    if not files:
        logger.info("[Ingest] No KB files | index=%s | volume=%s", index_key, auditor_volume)
        return

    # Only compare hashes when the auditor already has data; a fresh/empty index ingests everything.
    change_detect = index_manager.is_populated_for_auditor(index_key, auditor_id)
    index_manager.ensure_index(index_key)
    if change_detect:
        logger.info(
            "[Ingest] KB already populated — re-ingesting only changed/new files | index=%s | auditor=%s",
            index_key, auditor_id,
        )

    ocr_provider = _build_ocr_provider(settings)
    loader  = DocumentLoader(ocr_provider, translator=TranslationService(settings), settings=settings)
    chunker = Chunker(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"audit_kb_{index_key}_"))
    total = 0
    try:
        with ThreadPoolExecutor(max_workers=_INGEST_FILE_WORKERS) as pool:
            futures = {
                pool.submit(
                    _process_and_upsert_file,
                    file_info=f,
                    base_volume_path=auditor_volume,
                    index_key=index_key,
                    doc_type=DocumentType.SUPPORTING_DOC,
                    doc_source=doc_source,
                    scope_id=doc_source.value,
                    auditor_folder=auditor_id,
                    loader=loader,
                    chunker=chunker,
                    index_manager=index_manager,
                    downloader=downloader,
                    tmp_dir=tmp_dir,
                    change_detect=change_detect,
                ): f["name"]
                for f in files
            }
            for fut in as_completed(futures):
                try:
                    total += fut.result(timeout=300)
                except Exception as exc:
                    logger.warning("[Ingest] KB worker error | index=%s | error=%s", index_key, exc)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    logger.info(
        "[Ingest] KB done | index=%s | auditor=%s | files=%d | chunks=%d",
        index_key, auditor_id, len(files), total,
    )
    index_manager.wait_until_ready(index_key)


def _ensure_empty_index(index_manager, index_key: str) -> None:
    """Create the index now (empty) so it is ready for future ingestion. Best-effort.

    Used for local_policies, which currently has no dedicated data (local policies are consumed
    within the supporting-docs index); the index is created now so the team can upload to it later.
    """
    try:
        index_manager.ensure_index(index_key)
        logger.info("[Ingest] Index ensured (empty — ready for future data) | index=%s", index_key)
    except Exception as exc:
        logger.warning("[Ingest] Could not ensure index | index=%s | error=%s", index_key, exc)


def _ensure_kb_indexes(index_manager, settings, auditor_id: str, on_phase=None) -> None:
    """Ingest the auditor's past-audit + SI/Legal knowledge-base volumes up front, so this
    institutional knowledge is embedded as part of the initial setup (alongside supporting docs).

    Local policies are consumed within the supporting-docs index (not separately), so they are not
    ingested here. Best-effort: failures log and never abort the audit run.
    """
    # Full per-auditor folders — explicit API path when supplied, else <base volume>/<auditor_id>.
    kb_volumes = [
        ("past_audit",       settings.resolved_past_audit_folder,       DocumentSource.PAST_AUDIT),
        ("si_history",       settings.resolved_si_history_folder,       DocumentSource.SI_HISTORY),
        ("legal",            settings.resolved_legal_folder,            DocumentSource.LEGAL),
        ("additional_input", settings.resolved_additional_input_folder, DocumentSource.ADDITIONAL_INPUT),
    ]
    # Map each KB source to the status phase shown while it ingests.
    _kb_phase = {"past_audit": "RA_INGEST_PAST_AUDITS", "si_history": "RA_INGEST_SI_LEGAL",
                 "legal": "RA_INGEST_SI_LEGAL"}
    for index_key, auditor_volume, source in kb_volumes:
        if on_phase is not None and index_key in _kb_phase:
            try:
                on_phase(_kb_phase[index_key], current=None, total=None)
            except Exception:  # noqa: BLE001
                pass
        try:
            _ensure_kb_volume_index(
                index_manager, settings,
                index_key=index_key, auditor_volume=auditor_volume,
                auditor_id=auditor_id, doc_source=source,
            )
        except Exception as exc:
            logger.error("[Ingest] KB ingest failed | index=%s | error=%s", index_key, exc, exc_info=True)


def _write_scope_txn_summary(transactional_agent, output_writer, output_path) -> None:
    """Build and write the per-scope transaction summary sheet.

    Every canonical scope area appears; scopes with no transactions render '-'. USD is converted to the
    audit's local currency (resolved at runtime from the data, not a fixed config) at each transaction
    date's FX rate — no invented or fixed rates; local shown only when a real rate resolves.
    """
    import datetime as _dt

    from src.config.constants import SCOPE_ID_TO_DISPLAY_NAME
    from src.services.fx_rates import FxRateService

    summaries = transactional_agent.build_scope_transaction_summary()
    by_scope = {s.scope_id: s for s in summaries}

    local_code = transactional_agent.resolve_local_currency()

    all_days: list[_dt.date] = []
    for s in summaries:
        for day_str in list(s.usd_by_date) + list(s.doc_usd_by_date):
            if day_str:
                try:
                    all_days.append(_dt.date.fromisoformat(day_str))
                except ValueError:
                    pass

    fx = FxRateService(base="USD", local=local_code)
    if all_days:
        fx.load_period(min(all_days), max(all_days))
    latest_day = max(all_days) if all_days else None

    def _local_total(usd_by_date: dict) -> float | None:
        """Sum USD→local at each date's FX rate; None if any date cannot be converted."""
        total = 0.0
        for day_str, usd in usd_by_date.items():
            day = None
            if day_str:
                try:
                    day = _dt.date.fromisoformat(day_str)
                except ValueError:
                    day = None
            conv_day = day or latest_day
            converted = fx.convert(usd, conv_day) if conv_day else None
            if converted is None:
                return None
            total += converted
        return total

    def _amount_cell(usd_by_date: dict, usd_total: float, present: bool) -> str:
        if not present:
            return "-"
        # A count with no monetary data (e.g. a name-list supporting doc that has entries but no money
        # column) — show "-" for the amount rather than a misleading "USD 0.00".
        if not usd_by_date and not usd_total:
            return "-"
        local = _local_total(usd_by_date)
        if local is None:
            return f"USD {usd_total:,.2f}"
        return f"{local_code} {local:,.2f} / USD {usd_total:,.2f}"

    rows: list[tuple] = []
    for scope_id, display in SCOPE_ID_TO_DISPLAY_NAME.items():
        summary = by_scope.get(scope_id)
        if summary is None:
            rows.append((display, "-", "-", "-", "-", "-", "-", "-", "-"))
            continue

        table_present = summary.txn_count > 0 or bool(summary.usd_by_date)
        doc_present = summary.doc_txn_count > 0 or bool(summary.doc_usd_by_date)

        # Transaction counts split by origin + combined total.
        table_txns = summary.txn_count if table_present else "-"
        doc_txns = summary.doc_txn_count if doc_present else "-"
        total_txns = (summary.txn_count + summary.doc_txn_count) if (table_present or doc_present) else "-"

        table_amt = _amount_cell(summary.usd_by_date, summary.total_usd, table_present)
        doc_amt = _amount_cell(summary.doc_usd_by_date, summary.doc_total_usd, doc_present)

        # Total amount across both origins.
        if table_present or doc_present:
            combined_by_date: dict = {}
            for d, u in list(summary.usd_by_date.items()) + list(summary.doc_usd_by_date.items()):
                combined_by_date[d] = combined_by_date.get(d, 0.0) + u
            total_amt = _amount_cell(
                combined_by_date, summary.total_usd + summary.doc_total_usd, True
            )
        else:
            total_amt = "-"

        rows.append((
            display,
            table_txns,
            doc_txns,
            total_txns,
            table_amt,
            doc_amt,
            total_amt,
            "; ".join(summary.table_sources) if summary.table_sources else "-",
            "; ".join(summary.doc_sources) if summary.doc_sources else "-",
        ))

    output_writer.write_scope_txn_summary(rows, output_path)
    logger.info("[Main] Scope txn summary written | scopes=%d | local=%s", len(rows), local_code)


def _build_questionnaire_records(questions) -> list[dict]:
    """Build questionnaire-index records (Scoping-Deck schema) from the loaded AuditQuestions."""
    records: list[dict] = []
    for q in questions:
        content = (
            f"Q: {q.question_text}\nResponse: {q.response_text}"
            + (f"\nFollow-up Q: {q.followup_question}" if q.followup_question else "")
            + (f"\nFollow-up Response: {q.followup_response}" if q.followup_response else "")
        )
        records.append({
            "chunk_id": f"{q.auditor_id}::{q.questionnaire_type}::{q.question_id}",
            "content": content,
            "question_id": q.question_id,
            "question_text": q.question_text,
            "response_text": q.response_text,
            "followup_question": q.followup_question or "",
            "followup_response": q.followup_response or "",
            "scope_id": q.scope_id or "",
            "auditor_id": q.auditor_id or "",
            "questionnaire_type": q.questionnaire_type or "",
            "source_table": getattr(q, "source_table", "") or "",
            "metadata_json": "{}",
        })
    return records


def _load_questionnaire_from_volume(settings, auditor_id: str) -> list[dict]:
    """Load Q&A records from the questionnaire Databricks Volume path using the SDK (no SQL warehouse).

    Returns a list of questionnaire-index records ready for upsert. Returns [] if the path is not
    configured, the directory is empty, or any SDK error occurs.
    """
    vol_path = (getattr(settings, "questionnaire_volume_path", "") or "").strip()
    if not vol_path:
        return []

    try:
        import io
        import pandas as pd
        from databricks.sdk import WorkspaceClient

        from src.config.databricks_auth import build_config

        # Service-principal OAuth config (auto-refreshing) — NOT WorkspaceClient(host, token=…), which
        # conflicts with the env SP creds ("more than one authorization method: oauth and pat").
        client = WorkspaceClient(config=build_config(settings))
        auditor_path = f"{vol_path.rstrip('/')}/{auditor_id}"

        try:
            entries = list(client.files.list_directory_contents(directory_path=auditor_path))
        except Exception:
            entries = list(client.files.list_directory_contents(directory_path=vol_path.rstrip("/")))

        supported = {".parquet", ".json", ".csv", ".xlsx"}
        file_entries = [e for e in entries if not e.is_directory
                        and any((e.path or "").endswith(ext) for ext in supported)]

        if not file_entries:
            logger.info("[Ingest] No questionnaire files in volume — skipping | path=%s", vol_path)
            return []

        frames: list[pd.DataFrame] = []
        for entry in file_entries:
            try:
                raw = client.files.download(file_path=entry.path or "")
                data = raw.contents.read()
                fp = entry.path or ""
                if fp.endswith(".parquet"):
                    df = pd.read_parquet(io.BytesIO(data))
                elif fp.endswith(".json"):
                    df = pd.read_json(io.BytesIO(data))
                elif fp.endswith(".xlsx"):
                    df = pd.read_excel(io.BytesIO(data))
                else:
                    df = pd.read_csv(io.BytesIO(data))
                logger.info("[Ingest] Read questionnaire file | file=%s | rows=%d | cols=%s",
                            (entry.path or "").split("/")[-1], len(df), list(df.columns))
                frames.append(df)
            except Exception as exc:
                logger.warning("[Ingest] Skipping unreadable questionnaire file %s | %s", entry.path, exc)

        if not frames:
            return []

        df_all = pd.concat(frames, ignore_index=True)
        col_map = {c.lower().replace(" ", "_"): c for c in df_all.columns}

        # NOTE: this loader normalises column names with .lower().replace(" ", "_"), so multi-word
        # aliases below are the UNDERSCORE form (e.g. "management_response" for "Management Response").
        # Two source schemas: IDR (question="Data and Report Request", response="Management Response",
        # follow-ups="FollowUp Question/Response") and Planning (question="Question",
        # response="Company Response").
        _ALIASES = {
            "question_id":        ["question_id", "q_id", "qid", "id", "question_no"],
            "question_text":      ["question_text", "question", "q_text", "q", "data_and_report_request"],
            "response_text":      ["response_text", "response", "company_response", "management_response", "answer", "resp"],
            "followup_question":  ["followup_question", "follow_up_question", "followup_q"],
            "followup_response":  ["followup_response", "follow_up_response", "followup_a"],
            "scope_id":           ["scope_id", "scope"],
            "questionnaire_type": ["questionnaire_type", "type", "qtype"],
            "source_table":       ["source_table", "source", "file_name"],
        }

        def _get(row, field, default=""):
            for alias in _ALIASES.get(field, [field]):
                actual = col_map.get(alias.lower())
                if actual and actual in row.index:
                    val = row[actual]
                    if val is not None and str(val).strip() not in ("", "nan", "None"):
                        return str(val).strip()
            return default

        records = []
        for idx, row in df_all.iterrows():
            q_id   = _get(row, "question_id") or str(idx)
            q_text = _get(row, "question_text")
            r_text = _get(row, "response_text")
            if not q_text and not r_text:
                continue
            fupq = _get(row, "followup_question")
            fupr = _get(row, "followup_response")
            content = f"Q: {q_text}\nResponse: {r_text}"
            if fupq:
                content += f"\nFollow-up Q: {fupq}"
            if fupr:
                content += f"\nFollow-up Response: {fupr}"
            records.append({
                "chunk_id":          f"{auditor_id}::volume::{q_id}",
                "content":           content,
                "question_id":       q_id,
                "question_text":     q_text,
                "response_text":     r_text,
                "followup_question": fupq,
                "followup_response": fupr,
                "scope_id":          _get(row, "scope_id", settings.current_scope_id),
                "auditor_id":        auditor_id,
                "questionnaire_type": _get(row, "questionnaire_type", "idr"),
                "source_table":      _get(row, "source_table", vol_path),
                "metadata_json":     "{}",
            })

        logger.info("[Ingest] Loaded %d questionnaire record(s) from volume | path=%s", len(records), vol_path)
        return records

    except Exception as exc:
        logger.warning("[Ingest] Questionnaire volume load failed — skipping | %s", exc)
        return []


def _ensure_questionnaire_index(index_manager, settings, auditor_id: str, questions=None) -> None:
    """Populate the SHARED questionnaire index.

    PRIMARY source = the API-supplied questionnaire TABLES (the same planning/idr tables the audit
    questions are loaded from) — passed in as `questions`. The questionnaire-volume path is only a
    FALLBACK for deployments that still stage Q&A as files in a Volume.

    Idempotent per auditor; best-effort — failures log and never abort the run.
    """
    try:
        if index_manager.is_questionnaire_populated(auditor_id):
            logger.info(
                "[Ingest] questionnaire index already populated for auditor — skipping | auditor=%s",
                auditor_id,
            )
            return
        if questions:
            records = _build_questionnaire_records(questions)
            source = "tables"
        else:
            records = _load_questionnaire_from_volume(settings, auditor_id)
            source = "volume"
        if not records:
            logger.info("[Ingest] No questionnaire records to embed | auditor=%s | source=%s",
                        auditor_id, source)
            return
        index_manager.ensure_questionnaire_index()
        index_manager.upsert_questionnaire(records)
        logger.info(
            "[Ingest] questionnaire index populated | auditor=%s | records=%d | source=%s",
            auditor_id, len(records), source,
        )
    except Exception as exc:
        logger.error("[Ingest] questionnaire index ingest failed | error=%s", exc, exc_info=True)


def _build_recurrence_context(index_manager, settings, auditor_id: str) -> str:
    """Return a compact recurrence-context block (past-audit + SI History + Legal + Additional Input) for injection into Phase 2.

    Assumes the KB indexes are already ingested (see _ensure_kb_indexes). Best-effort: any failure
    yields an empty string so the audit run still proceeds.
    """
    def _fetch(index_key: str):
        try:
            return index_manager.similarity_search(
                index_key=index_key,
                query="compliance risk observation",
                scope_id=None,
                top_k=_KB_CONTEXT_TOP_K,
                filters={"auditor_folder": auditor_id},
            )
        except Exception as exc:
            logger.warning("[Main] KB fetch skipped | index=%s | error=%s", index_key, exc)
            return []

    si_legal_chunks = _fetch("si_history") + _fetch("legal")
    additional_chunks = _fetch("additional_input")
    return format_prior_context(_fetch("past_audit"), si_legal_chunks, additional_chunks)


def _persist_raw_tables_index(index_manager, transactional_agent, auditor_id: str) -> None:
    """Store the 7 dashboard tables' schemas into the raw-transactional-tables index (auditor-scoped).

    Idempotent and best-effort: failures log and never abort the audit run.
    """
    try:
        if index_manager.is_populated_for_auditor("raw_transactional", auditor_id):
            logger.info(
                "[Main] Raw-transactional-tables already embedded for auditor — skipping | auditor=%s",
                auditor_id,
            )
            return
        table_info = transactional_agent.pull_table_info()
        chunks = build_raw_table_chunks(table_info, auditor_id)
        if not chunks:
            return
        index_manager.ensure_index("raw_transactional")
        index_manager.upsert_chunks("raw_transactional", chunks)
        index_manager.wait_until_ready("raw_transactional")
        logger.info(
            "[Main] Raw-transactional-tables index persisted | auditor=%s | tables=%d",
            auditor_id, len(chunks),
        )
    except Exception as exc:
        logger.error("[Main] Raw-tables index persist failed | error=%s", exc, exc_info=True)


def _persist_transactional_index(index_manager, findings, auditor_id: str) -> None:
    """Store Phase-3 findings into the transactional-analysis index, scoped to this auditor.

    Idempotent: the auditor's existing entries are cleared before re-persisting so a re-run
    refreshes rather than duplicates. Failures are logged and never abort the audit run.
    """
    if not findings:
        return
    try:
        if index_manager.is_populated_for_auditor("transactional_analysis", auditor_id):
            logger.info(
                "[Main] Transactional-analysis already embedded for auditor — skipping | auditor=%s",
                auditor_id,
            )
            return
        index_manager.ensure_index("transactional_analysis")
        chunks = build_transactional_chunks(findings, auditor_id)
        index_manager.upsert_chunks("transactional_analysis", chunks)
        index_manager.wait_until_ready("transactional_analysis")
        logger.info(
            "[Main] Transactional-analysis index persisted | auditor=%s | chunks=%d",
            auditor_id, len(chunks),
        )
    except Exception as exc:
        logger.error("[Main] Transactional-analysis index persist failed | error=%s", exc, exc_info=True)


async def run_audit(auditor_id: str, output_path: Path, on_phase=None) -> None:
    settings = get_settings()
    logger.info("[Main] Starting | scope=%s | auditor=%s", settings.current_scope_id, auditor_id)
    log_prompt_versions(logger)  # traceability: which prompt versions produced this run's output

    def _phase(code: str, current: int | None = None, total: int | None = None) -> None:
        """Publish the current RA_* phase to the run status (no-op when run without a reporter)."""
        if on_phase is None:
            return
        try:
            on_phase(code, current=current, total=total)
        except Exception:  # noqa: BLE001 — status reporting must never break the audit
            pass

    llm_provider       = AzureOpenAILLMProvider(settings)
    embedding_provider = AzureOpenAIEmbeddingProvider(settings)
    llm        = llm_provider.get_client()
    embeddings = embedding_provider.get_client()

    # ── Observability (Lakebase run tracing) — a complete no-op unless obs_enabled ─
    recorder = ObservabilityRecorder(settings)
    recorder.ensure_schema()
    tracer = ObservabilityTracer(recorder, default_model=settings.azure_openai_deployment)
    if recorder.enabled:
        try:
            existing = list(getattr(llm, "callbacks", None) or [])
            llm.callbacks = existing + [tracer]
        except Exception as exc:
            logger.warning("[Obs] Could not attach tracer to LLM — call tracing off | error=%s", exc)
    run_started = datetime.now(timezone.utc)
    run_id = recorder.start_run(
        auditor_id=auditor_id,
        model=settings.azure_openai_deployment,
        prompt_bundle_id=prompt_bundle_id(),
        prompt_versions={
            name: f"{info['version']}#{info['hash']}"
            for name, info in get_prompt_versions().items()
        },
        run_config={
            "question_concurrency": QUESTION_CONCURRENCY,
            "matrix_band_high_min": MATRIX_BAND_HIGH_MIN,
            "matrix_band_medium_min": MATRIX_BAND_MEDIUM_MIN,
            "foreign_official_impact_floor": FOREIGN_OFFICIAL_IMPACT_FLOOR,
            "missing_evidence_likelihood_floor": MISSING_EXPECTED_EVIDENCE_LIKELIHOOD_FLOOR,
            "recurrence_likelihood_increment": RECURRENCE_LIKELIHOOD_INCREMENT,
            "current_scope_id": settings.current_scope_id,
        },
    )

    # Hand the ingest workers what they need to write obs_ingestion rows (set once, before the
    # thread pool starts; read-only during ingestion). No-op when observability is disabled.
    _INGEST_OBS["recorder"] = recorder if recorder.enabled else None
    _INGEST_OBS["run_id"] = run_id
    _INGEST_OBS["embedding_model"] = settings.azure_embedding_deployment
    _INGEST_OBS["embedding_dimensions"] = settings.embedding_dimensions

    _phase("RA_INGEST_SUPPORTING")
    index_manager = VectorIndexManager(settings, embedding_provider)
    _ensure_indexes(index_manager, embedding_provider, settings)
    _ensure_supporting_docs_index(index_manager, settings, auditor_id)
    _ensure_scope_policy_index(index_manager, settings)
    _ensure_empty_index(index_manager, "local_policies")
    # Past-audit + SI/Legal knowledge — embedded up front as part of the initial knowledge base.
    # (_ensure_kb_indexes emits RA_INGEST_PAST_AUDITS + RA_INGEST_SI_LEGAL as it ingests each.)
    _ensure_kb_indexes(index_manager, settings, auditor_id, on_phase=_phase)
    # Questionnaire index is populated later from the API-supplied TABLES (after the questions load),
    # not here — see the _ensure_questionnaire_index(..., questions=all_questions) call below.

    # Download reference files (GL map + TPI inventory), resolve the auditor's inventory sheet,
    # and build the TPI inventory block used for the classifier's GATE B same-party recognition.
    _phase("RA_INGEST_POLICIES")
    tpi_inventory_block = _prepare_reference_files(settings, auditor_id, llm=llm)

    hcbi_tool         = HcbiRetrievalTool(index_manager, settings, llm=llm)
    scope_policy_tool = ScopePolicyRetrievalTool(index_manager, settings)
    dd_policy_tool    = PolicyRetrievalTool(index_manager, settings, llm=llm)
    evidence_tool     = EvidenceRetrievalTool(index_manager, settings, auditor_folder=auditor_id, llm=llm)

    scope_identifier_agent   = ScopeIdentifierAgent(llm)
    hcbi_policy_agent        = HcbiPolicyAgent(llm, hcbi_tool)
    policy_knowledge_agent   = PolicyKnowledgeAgent(llm, scope_policy_tool, dd_policy_tool)
    supporting_docs_agent    = SupportingDocsAgent(llm, evidence_tool)
    risk_evaluation_agent    = RiskEvaluationAgent(llm)
    transactional_data_agent = TransactionalDataAgent(llm, settings)
    risk_classifier_agent    = RiskClassifierAgent(llm)
    risk_writer_agent        = RiskWriterAgent(llm)

    transactional_analysis_agent = TransactionalDataAnalysisAgent(llm, settings)

    # ── Phase 3 runs FIRST — the current-year transactional risks become pre-context for the
    #    per-question analysis (Phase 2), so responses are evaluated against current-year data.
    tracer.set_stage_default(ObsContext(
        run_id=run_id, stage=OBS_STAGE_TRANSACTIONAL_ANALYSIS, agent_name=AGENT_TRANSACTIONAL_ANALYSIS,
    ))
    _phase("RA_ANALYZE_TRANSACTIONS")
    analysis_findings: list[RiskOutput] = []
    try:
        analysis_findings = transactional_analysis_agent.run_all()
        logger.info("[Main] Phase 3 (pre-analysis) complete | findings=%d", len(analysis_findings))
    except Exception as exc:
        logger.error("[Main] Phase 3 failed | error=%s", exc, exc_info=True)

    transactional_context = format_transactional_context(analysis_findings)
    _persist_transactional_index(index_manager, analysis_findings, auditor_id)
    _persist_raw_tables_index(index_manager, transactional_analysis_agent, auditor_id)

    # Past-audit + SI/Legal knowledge — ingested per auditor and injected as HISTORICAL recurrence
    # context (never a current-year risk on its own).
    recurrence_context = _build_recurrence_context(index_manager, settings, auditor_id)

    supervisor = build_audit_graph(
        llm=llm,
        scope_identifier_agent=scope_identifier_agent,
        hcbi_policy_agent=hcbi_policy_agent,
        policy_knowledge_agent=policy_knowledge_agent,
        supporting_docs_agent=supporting_docs_agent,
        risk_evaluation_agent=risk_evaluation_agent,
        transactional_data_agent=transactional_data_agent,
        risk_classifier_agent=risk_classifier_agent,
        risk_writer_agent=risk_writer_agent,
        tpi_inventory_block=tpi_inventory_block,
        transactional_context=transactional_context,
        recurrence_context=recurrence_context,
        audit_context=settings.audit_scope_context,
    )
    if settings.audit_scope_context:
        logger.info("[Runner] Audit context injected | %s", settings.audit_scope_context)

    conflict_detector  = ConflictDetector(embeddings, settings)
    questionnaire_repo = DatabricksQuestionnaireRepository(settings, llm=llm)

    all_questions = []
    # One or more tables per type (API-supplied list, else the single configured table).
    for qtype, tables in (
        ("planning", settings.planning_table_list),
        ("idr", settings.idr_table_list),
    ):
        for table in tables:
            qs = questionnaire_repo.load_all(
                scope_id=settings.current_scope_id,
                auditor_id=auditor_id,
                questionnaire_type=qtype,
                table=table,
            )
            logger.info("[Main] Loaded %d %s questions | table=%s", len(qs), qtype, table)
            all_questions.extend(qs)
    logger.info("[Main] Total questions to process: %d (planning + idr)", len(all_questions))

    # Populate the questionnaire index from the API-supplied TABLES just loaded (used later for
    # recurrence/corroboration). Idempotent: skips when already populated (e.g. from the volume path).
    _ensure_questionnaire_index(index_manager, settings, auditor_id, questions=all_questions)

    # Identify the audit's LOCAL language from the questionnaire responses (which are kept in their
    # original language) so Output A can render bilingual answers. An explicit AUDIT_LOCAL_LANGUAGE
    # always wins; otherwise it is auto-detected here and set on settings for the OutputWriter to use.
    if not (settings.audit_local_language or "").strip():
        sample = "\n".join(
            f"{q.question_text} {q.response_text}" for q in all_questions[:60]
        )
        detected = TranslationService.detect_language(sample)
        if detected:
            settings.audit_local_language = detected
            logger.info("[Main] Local language auto-detected = %s — Output A will be bilingual", detected)
        else:
            logger.info("[Main] No local language detected — Output A stays English-only")

    recorder.set_total_questions(run_id, len(all_questions))
    tracer.set_stage_default(per_question_default(run_id))

    semaphore         = asyncio.Semaphore(QUESTION_CONCURRENCY)
    completed_states: list[AuditState] = []
    completed_count   = 0
    lock              = asyncio.Lock()
    output_writer     = OutputWriter(OutputRepository(), settings)
    # Register the auditor's actually-indexed supporting-doc names so the writer drops phantom
    # Supporting-Evidence citations (a file cited by the LLM but never ingested). Best-effort /
    # fail-open: if the lookup returns nothing, no filtering is applied.
    try:
        output_writer.set_valid_supporting_files(
            index_manager.list_document_names("supporting", auditor_id)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Main] Could not list supporting doc names — phantom-citation filter off | %s", exc)

    async def process_question(question, idx: int):
        nonlocal completed_count
        async with semaphore:
            logger.info(
                "[Main] Processing [%d/%d] | id=%s | type=%s",
                idx, len(all_questions), question.question_id, question.questionnaire_type,
            )
            q_started = datetime.now(timezone.utc)
            question_uid = recorder.start_question(
                run_id=run_id,
                question_index=_as_int(question.question_id, idx),
                source_file=question.questionnaire_type,
                question_hash=_hash_text(question.question_text),
            )
            try:
                with observe(run_id=run_id, question_uid=question_uid, stage=OBS_STAGE_PER_QUESTION):
                    supervisor_output = await supervisor.run(
                        question_text=question.question_text,
                        response_text=question.response_text,
                        scope_id=question.scope_id,
                        questionnaire_type=question.questionnaire_type,
                        followup_question=question.followup_question,
                        followup_response=question.followup_response,
                    )
                async with lock:
                    state = await asyncio.to_thread(
                        conflict_detector.detect_and_flag, AuditState(question=question)
                    )
                    state.risk_finding.ai_recommendation = supervisor_output.content
                    state.risk_finding.calculation_evidence = supervisor_output.calculation_evidence
                    state.processing_complete = True
                    completed_states.append(state)
                    completed_count += 1
                    output_writer.write_one(state, sno=completed_count, output_path=output_path)
                    _phase("RA_PROCESS_QUESTIONS", current=completed_count, total=len(all_questions))
                    logger.info(
                        "[Main] Saved [%d/%d] → %s",
                        completed_count, len(all_questions), output_path.name,
                    )
                recorder.finish_question(
                    question_uid=question_uid,
                    status=OBS_QSTATUS_COMPLETED,
                    started_at=q_started,
                    assigned_scope=supervisor_output.assigned_scope,
                    final_risk_category=supervisor_output.risk_category,
                    final_matrix_score=supervisor_output.matrix_score,
                    likelihood=supervisor_output.likelihood,
                    impact=supervisor_output.impact,
                    llm_confidence=supervisor_output.llm_confidence,
                    derived_confidence=supervisor_output.derived_confidence,
                    transactional_invoked=supervisor_output.transactional_invoked,
                )
            except Exception as exc:
                recorder.finish_question(
                    question_uid=question_uid,
                    status=OBS_QSTATUS_ERROR,
                    started_at=q_started,
                    error=str(exc),
                )
                async with lock:
                    completed_count += 1
                    output_writer.write_manual_review(
                        question, sno=completed_count, reason=str(exc), output_path=output_path,
                    )
                    _phase("RA_PROCESS_QUESTIONS", current=completed_count, total=len(all_questions))
                logger.error(
                    "[Main] Flagged for MANUAL REVIEW (analysis failed) | id=%s | error=%s",
                    question.question_id, exc, exc_info=True,
                )

    _phase("RA_PROCESS_QUESTIONS", current=0, total=len(all_questions))
    await asyncio.gather(*[
        process_question(q, i + 1) for i, q in enumerate(all_questions)
    ])

    logger.info(
        "[Main] Phase 1&2 complete | processed=%d/%d",
        len(completed_states), len(all_questions),
    )

    output_writer.write(
        states=completed_states,
        analysis_findings=analysis_findings,
        scope_name="FCPA Compliance (All Scope Areas)",
        output_path=output_path,
    )

    # ── Aggregation (risk-centric third sheet) — cluster per-question + transactional findings
    #    into unique risks, set Past Occurrence via the per-auditor KB, and roll up per scope.
    _phase("RA_GENERATE_SUMMARY")
    logger.info("[Main] Aggregation stage starting…")
    tracer.set_stage_default(ObsContext(
        run_id=run_id, stage=OBS_STAGE_AGGREGATION, agent_name=AGENT_RISK_AGGREGATION,
        prompt_name="risk_aggregation",
    ))
    try:
        output_rows = [
            output_writer.row_for(state, sno=i + 1)
            for i, state in enumerate(completed_states)
        ]
        aggregation_agent = RiskAggregationAgent(llm)
        retriever = KnowledgeRetriever(index_manager, auditor_id)
        aggregated_risks, scope_overall = run_aggregation(
            aggregation_agent, retriever, output_rows, analysis_findings,
            recorder=recorder, run_id=run_id,
        )
        output_writer.write_aggregation(aggregated_risks, scope_overall, output_path)
        logger.info("[Main] Aggregation complete | risks=%d", len(aggregated_risks))
        # Cross-run continuity: flag scopes whose risk set changed vs the most recent prior run.
        # Best-effort and fully guarded — no-op when observability is off or no prior run exists.
        try:
            prior_summary = recorder.load_prior_scope_summary(auditor_id, exclude_run_id=run_id)
            continuity_notes = compute_continuity(prior_summary, aggregated_risks)
            if continuity_notes:
                logger.info("[Continuity] %d scope(s) changed vs the prior run:\n  %s",
                            len(continuity_notes), "\n  ".join(continuity_notes))
                write_continuity_report(continuity_notes, output_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Continuity] Cross-run comparison skipped | %s", exc)
    except Exception as exc:
        logger.error("[Main] Aggregation stage failed | error=%s", exc, exc_info=True)

    # ── Scope transaction summary sheet (per-scope count + local/USD totals; FX per transaction date)
    logger.info("[Main] Scope transaction summary stage starting…")
    try:
        _write_scope_txn_summary(transactional_analysis_agent, output_writer, output_path)
    except Exception as exc:
        logger.error("[Main] Scope txn summary stage failed | error=%s", exc, exc_info=True)

    # ── Past Audit Report sheet — summarise the auditor's past-audit index (one row per observation)
    _phase("RA_SUMMARIZE_PAST_AUDITS")
    logger.info("[Main] Past Audit stage starting…")
    tracer.set_stage_default(ObsContext(
        run_id=run_id, stage=OBS_STAGE_PAST_AUDIT, agent_name=AGENT_PAST_AUDIT_SUMMARY,
        prompt_name="past_audit_summary",
    ))
    try:
        past_audit_agent = PastAuditSummarizerAgent(llm)
        observations = summarize_past_audits(index_manager, past_audit_agent, auditor_id)
        output_writer.write_past_audit(observations, output_path)
        logger.info("[Main] Past Audit sheet complete | observations=%d", len(observations))
    except Exception as exc:
        logger.error("[Main] Past Audit stage failed | error=%s", exc, exc_info=True)

    run_status = (
        OBS_STATUS_COMPLETED
        if len(completed_states) == len(all_questions)
        else OBS_STATUS_PARTIAL
    )
    recorder.finish_run(run_id=run_id, status=run_status, started_at=run_started)
    recorder.close()

    logger.info(
        "[Main] Complete | processed=%d/%d | analysis_findings=%d | output=%s",
        len(completed_states), len(all_questions), len(analysis_findings), output_path.name,
    )


async def main() -> None:
    from src.utils.notice_filter import suppress_pat_notice
    suppress_pat_notice()

    auditor_id = os.environ.get("AUDITOR_ID", "").strip()
    if not auditor_id:
        raise RuntimeError(
            "AUDITOR_ID environment variable is not set. "
            "Set it to the auditor's folder name (e.g. AUDITOR_ID=Sam_1)."
        )
    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = _PROJECT_ROOT / "output" / f"FCPA_Audit_{auditor_id}_{run_ts}.xlsx"
    await run_audit(auditor_id=auditor_id, output_path=output_path)
