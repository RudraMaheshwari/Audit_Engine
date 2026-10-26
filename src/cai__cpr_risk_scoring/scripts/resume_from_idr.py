import asyncio
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent


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

import openpyxl

from src.agents.hcbi_policy_agent import HcbiPolicyAgent
from src.agents.policy_knowledge_agent import PolicyKnowledgeAgent
from src.agents.risk_classifier_agent import RiskClassifierAgent
from src.agents.risk_evaluation_agent import RiskEvaluationAgent
from src.agents.risk_writer_agent import RiskWriterAgent
from src.agents.scope_identifier_agent import ScopeIdentifierAgent
from src.agents.supporting_docs_agent import SupportingDocsAgent
from src.agents.transactional_data_agent import TransactionalDataAgent
from src.agents.transactional_data_analysis_agent import TransactionalDataAnalysisAgent
from src.config.constants import HCBI_SCOPE_ID, QUESTION_CONCURRENCY, SHEET_A_NAME
from src.config.settings import get_settings
from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider
from src.ingestion.chunker import Chunker
from src.ingestion.extractors.ocr.tesseract_provider import TesseractOCRProvider
from src.ingestion.loader import DocumentLoader
from src.ingestion.volume_downloader import download_volume_file, temp_volume_download
from src.models.schemas import AuditState, DocumentSource
from src.output.output_writer import OutputWriter
from src.pipeline.graph import build_audit_graph
from src.repositories.output_repo import OutputRepository
from src.repositories.questionnaire_repo import DatabricksQuestionnaireRepository
from src.schemas.state import DocumentType, RiskOutput
from src.services.conflict_detector import ConflictDetector
from src.services.tpi_inventory import resolve_auditor_sheet
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


def _resolve_output_file() -> Path:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        candidates = sorted(Path("output").glob("FCPA_Audit_*.xlsx"))
        if not candidates:
            logger.error("[Resume] No output file found in output/. Pass the path as an argument.")
            sys.exit(1)
        path = candidates[-1]
        logger.info("[Resume] No path given — using most recent: %s", path.name)
    if not path.exists():
        logger.error("[Resume] Output file not found: %s", path)
        sys.exit(1)
    return path


def _ensure_writable(path: Path) -> None:
    try:
        with open(path, "a+b"):
            pass
    except PermissionError:
        logger.error(
            "[Resume] '%s' is locked — close it in Excel and re-run.", path.name
        )
        sys.exit(1)


def _count_existing_rows(path: Path) -> int:
    try:
        wb = openpyxl.load_workbook(path, read_only=True)
        ws = wb[SHEET_A_NAME]
        count = max(ws.max_row - 1, 0)
        wb.close()
        logger.info("[Resume] Existing rows in file: %d — IDR will start from sno %d", count, count + 1)
        return count
    except Exception as exc:
        logger.error("[Resume] Could not read existing row count: %s", exc)
        sys.exit(1)


def _ensure_indexes(index_manager, embedding_provider, settings) -> None:
    ocr_provider = TesseractOCRProvider(tesseract_cmd=settings.tesseract_cmd)
    loader  = DocumentLoader(ocr_provider)
    chunker = Chunker(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)

    for index_key, doc_type, doc_source, volume_attr in _INDEX_PIPELINE:
        scope_id = HCBI_SCOPE_ID if index_key == "hcbi" else settings.current_scope_id

        if index_manager.is_populated(index_key, scope_id):
            logger.info("[Ingest] Index already populated — skipping | index=%s", index_key)
            continue

        index_manager.delete_index(index_key)
        index_manager.ensure_index(index_key)

        volume_path = getattr(settings, volume_attr)
        logger.info("[Ingest] Index empty — downloading from Volume | index=%s", index_key)

        with temp_volume_download(settings.databricks_host, settings.databricks_token, volume_path) as tmp_dir:
            try:
                documents = loader.load_directory(
                    root_path=tmp_dir,
                    scope_id=scope_id,
                    document_type=doc_type,
                )
            except FileNotFoundError:
                logger.warning("[Ingest] No files downloaded for index=%s", index_key)
                continue

        if not documents:
            logger.warning("[Ingest] No documents found — skipping | index=%s", index_key)
            continue

        all_chunks = []
        for doc in documents:
            all_chunks.extend(chunker.chunk(doc, doc_source))

        total_chunks = len(all_chunks)
        batches = [all_chunks[i : i + 50] for i in range(0, total_chunks, 50)]
        completed = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(index_manager.upsert_chunks, index_key, b): b for b in batches}
            for fut in as_completed(futures):
                fut.result(timeout=120)
                completed += len(futures[fut])
                logger.info(
                    "[Ingest] Upsert progress | index=%s | %d/%d chunks",
                    index_key, min(completed, total_chunks), total_chunks,
                )

        index_manager.wait_until_ready(index_key)


def _ensure_supporting_docs_index(index_manager, embedding_provider, settings) -> None:
    if index_manager.is_populated_any("supporting"):
        logger.info("[Ingest] supporting_docs index already populated — skipping")
        return

    index_manager.delete_index("supporting")
    index_manager.ensure_index("supporting")

    volume_path = settings.auditors_supporting_docs_volume_path
    logger.info("[Ingest] Downloading auditor supporting docs | volume=%s", volume_path)

    ocr_provider = TesseractOCRProvider(tesseract_cmd=settings.tesseract_cmd)
    loader  = DocumentLoader(ocr_provider)
    chunker = Chunker(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)

    all_chunks = []
    with temp_volume_download(settings.databricks_host, settings.databricks_token, volume_path) as tmp_dir:
        auditor_folders = [f for f in Path(tmp_dir).iterdir() if f.is_dir()]
        if not auditor_folders:
            logger.warning("[Ingest] No auditor subfolders found | volume=%s", volume_path)
            return

        for folder in sorted(auditor_folders):
            folder_name = folder.name
            try:
                documents = loader.load_directory(
                    root_path=folder,
                    scope_id="supporting",
                    document_type=DocumentType.SUPPORTING_DOC,
                )
            except FileNotFoundError:
                logger.warning("[Ingest] Empty auditor folder | folder=%s", folder_name)
                continue

            if not documents:
                continue

            folder_chunks = []
            for doc in documents:
                raw = chunker.chunk(doc, DocumentSource.SUPPORTING)
                tagged = [c.model_copy(update={"auditor_folder": folder_name}) for c in raw]
                folder_chunks.extend(tagged)

            logger.info(
                "[Ingest] Auditor folder chunked | folder=%s | docs=%d | chunks=%d",
                folder_name, len(documents), len(folder_chunks),
            )
            all_chunks.extend(folder_chunks)

    if not all_chunks:
        logger.warning("[Ingest] No chunks produced for supporting_docs index")
        return

    total = len(all_chunks)
    batches = [all_chunks[i : i + 50] for i in range(0, total, 50)]
    completed = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(index_manager.upsert_chunks, "supporting", b): b for b in batches}
        for fut in as_completed(futures):
            fut.result(timeout=120)
            completed += len(futures[fut])
            logger.info("[Ingest] Upsert progress | supporting | %d/%d chunks",
                        min(completed, total), total)

    logger.info("[Ingest] Done | supporting | total_chunks=%d", total)
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
        settings.databricks_token,
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


def _run_phase3(settings, output_path: Path, writer: OutputWriter, auditor_id: str = "") -> None:
    ref_dir = Path("output") / "_phase3_refs"
    ref_dir.mkdir(parents=True, exist_ok=True)
    gl_local  = ref_dir / Path(settings.hcc_gl_account_map_path).name
    tpi_local = ref_dir / Path(settings.tpi_inventory_file_path).name

    ok_gl = download_volume_file(
        settings.databricks_host, settings.databricks_token,
        settings.hcc_gl_account_map_path, gl_local,
    )
    ok_tpi = download_volume_file(
        settings.databricks_host, settings.databricks_token,
        settings.tpi_inventory_file_path, tpi_local,
    )

    if ok_gl:
        settings.hcc_gl_account_map_path = str(gl_local)
    if ok_tpi:
        settings.tpi_inventory_file_path = str(tpi_local)
    if not (ok_gl and ok_tpi):
        logger.warning(
            "[Phase3] One/both reference files unavailable — GL and/or supplier checks may be skipped."
        )

    # Auto-derive the auditor's TPI inventory sheet (unless an explicit override is configured).
    if not settings.tpi_inventory_sheet:
        resolved = resolve_auditor_sheet(settings.tpi_inventory_file_path, auditor_id)
        if resolved:
            settings.tpi_inventory_sheet = resolved
            logger.info("[Phase3] TPI inventory sheet for auditor '%s' -> %s", auditor_id, resolved)
        else:
            logger.warning(
                "[Phase3] Could not resolve TPI inventory sheet for auditor '%s' — TPI matching skipped",
                auditor_id,
            )

    llm = AzureOpenAILLMProvider(settings).get_client()
    agent = TransactionalDataAnalysisAgent(llm, settings)
    findings = agent.run(settings.current_scope_id)
    logger.info("[Phase3] Findings produced: %d", len(findings))

    if not findings:
        logger.warning("[Phase3] No findings — nothing appended for Phase 3.")
        return

    writer.write(
        states=[],
        scope_name="TPI Due Diligence",
        output_path=output_path,
        analysis_findings=findings,
    )
    logger.info("[Phase3] Appended %d finding(s) and refreshed summary sheet.", len(findings))


async def run_idr_resume(output_path: Path, auditor_id: str) -> None:
    settings = get_settings()
    logger.info("[Resume] Starting IDR resume | scope=%s | auditor=%s | file=%s", settings.current_scope_id, auditor_id, output_path.name)

    existing_row_count = _count_existing_rows(output_path)

    llm_provider       = AzureOpenAILLMProvider(settings)
    embedding_provider = AzureOpenAIEmbeddingProvider(settings)
    llm        = llm_provider.get_client()
    embeddings = embedding_provider.get_client()

    index_manager = VectorIndexManager(settings, embedding_provider)
    _ensure_indexes(index_manager, embedding_provider, settings)
    _ensure_supporting_docs_index(index_manager, embedding_provider, settings)
    _ensure_scope_policy_index(index_manager, settings)

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
    )

    conflict_detector  = ConflictDetector(embeddings, settings)
    questionnaire_repo = DatabricksQuestionnaireRepository(settings)

    idr_questions = questionnaire_repo.load_all(
        scope_id=settings.current_scope_id,
        auditor_id=auditor_id,
        questionnaire_type="idr",
    )
    logger.info("[Resume] Loaded %d IDR questions", len(idr_questions))

    if not idr_questions:
        logger.error("[Resume] No IDR questions found — check Databricks table and scope_id.")
        sys.exit(1)

    total_idr = len(idr_questions)

    semaphore      = asyncio.Semaphore(QUESTION_CONCURRENCY)
    completed_states: list[AuditState] = []
    idr_done       = 0
    lock           = asyncio.Lock()
    output_writer  = OutputWriter(OutputRepository())

    async def process_question(question, idx: int):
        nonlocal idr_done
        async with semaphore:
            logger.info(
                "[Resume] IDR [%d/%d] | id=%s",
                idx, total_idr, question.question_id,
            )
            try:
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
                    state.risk_finding.ai_recommendation = supervisor_output
                    state.processing_complete = True
                    completed_states.append(state)
                    idr_done += 1
                    sno = existing_row_count + idr_done
                    output_writer.write_one(state, sno=sno, output_path=output_path)
                    logger.info("[Resume] Saved IDR [%d/%d] → sno=%d → %s", idr_done, total_idr, sno, output_path.name)
            except Exception as exc:
                logger.error("[Resume] Failed | id=%s | error=%s", question.question_id, exc, exc_info=True)

    await asyncio.gather(*[
        process_question(q, i + 1) for i, q in enumerate(idr_questions)
    ])

    logger.info("[Resume] IDR complete | processed=%d/%d", len(completed_states), total_idr)

    logger.info("[Resume] Starting Phase 3 financial checks ...")
    _run_phase3(settings, output_path, output_writer, auditor_id=auditor_id)

    logger.info(
        "[Resume] All done | planning_rows=%d | idr_processed=%d | file=%s",
        existing_row_count, len(completed_states), output_path.name,
    )


if __name__ == "__main__":
    auditor_id = os.environ.get("AUDITOR_ID", "").strip()
    if not auditor_id:
        logger.error(
            "[Resume] AUDITOR_ID environment variable is not set. "
            "Set it to the auditor's folder name (e.g. AUDITOR_ID=Sam_1)."
        )
        sys.exit(1)
    output_path = _resolve_output_file()
    _ensure_writable(output_path)
    asyncio.run(run_idr_resume(output_path, auditor_id=auditor_id))
