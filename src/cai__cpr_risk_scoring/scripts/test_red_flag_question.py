"""Run ONLY the Due Diligence red-flag question through the full per-question risk-analysis pipeline,
for auditor Naeem_7 / engagement 20270117, and print whether the red-flag supporting-evidence Excel
files are pulled into the sources.

Purpose: cross-check the BM25-reranker fix — the mostly-Korean red-flag records
    "No.3_Red Flag with Mitigations by Third Party record.xlsx"
    "No.3_ Red Flag by Third Party record.xlsx"
should now survive the lexical re-rank and appear in SOURCE DETAILS / surfaced supporting docs.

Builds the SAME agents/tools/supervisor the real run uses (no ingestion — indexes must already exist).

Run (venv active, .env set with endpoint/creds as for a real run):
    python scripts/test_red_flag_question.py
    python scripts/test_red_flag_question.py --auditor Naeem_7 --engagement 20270117
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv((_ROOT / ".env") if (_ROOT / ".env").exists() else find_dotenv(usecwd=True))


def _fix_tls() -> None:
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001
        pass


_fix_tls()

# Red-flag supporting-evidence files we expect to be pulled (your WorkspaceClient query proved they are
# indexed for Naeem_7). The check at the end flags whether retrieval surfaced them.
_EXPECTED_DOCS = [
    "No.3_Red Flag with Mitigations by Third Party record.xlsx",
    "No.3_ Red Flag by Third Party record.xlsx",
]

# The Due Diligence red-flag IDR item ([283]/[284]).
_ITEM = {
    "type": "idr",
    "question": (
        "For the Due Diligence Process, please provide the following:\n"
        "a. Current TPI inventory, including all intermediaries used by the Company.\n"
        "b. List of any red flags that are still under mitigation and a brief description of the red "
        "flag.\n\nPlease ensure the TPI list contains a description of the TPI's responsibility "
        "(e.g., TPI, promotional sales agent, lobbyists)."
    ),
    "fq": (
        "Based on the list (No. 3 a_TPI Inventory_Overdue Report (General) - April 19, "
        "2026-20-40-41), there are 237 active TPIs. Could you please add a column to classify these "
        "TPIs into relevant categories (such as wholesalers, logistics providers, marketing agencies, "
        "consultants, etc.)?"
    ),
    "response": "Please find the attached file",
    "fr": "Please refer to the file uploaded in the [Additional file - June 1, 2026] folder",
}


async def _run() -> int:
    p = argparse.ArgumentParser(description="Run the Due Diligence red-flag question and show its sources.")
    p.add_argument("--auditor", default="Naeem_7")
    p.add_argument("--engagement", default="20270117")
    p.add_argument("--excel", default="test_red_flag_output.xlsx")
    args = p.parse_args()

    # Set auditor + engagement via env BEFORE settings load (get_settings is lru_cached).
    os.environ["AUDITOR_ID"] = args.auditor
    os.environ["AUDIT_ENGAGEMENT_NUMBERS"] = args.engagement

    from src.agents.hcbi_policy_agent import HcbiPolicyAgent
    from src.agents.policy_knowledge_agent import PolicyKnowledgeAgent
    from src.agents.risk_classifier_agent import RiskClassifierAgent
    from src.agents.risk_evaluation_agent import RiskEvaluationAgent
    from src.agents.risk_writer_agent import RiskWriterAgent
    from src.agents.scope_identifier_agent import ScopeIdentifierAgent
    from src.agents.supporting_docs_agent import SupportingDocsAgent
    from src.agents.transactional_data_agent import TransactionalDataAgent
    from src.config.settings import get_settings
    from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
    from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider
    from src.models.schemas import AuditQuestion, AuditState
    from src.output.output_writer import OutputWriter
    from src.pipeline.graph import build_audit_graph
    from src.repositories.output_repo import OutputRepository
    from src.tools.evidence_retrieval_tool import EvidenceRetrievalTool
    from src.tools.hcbi_retrieval_tool import HcbiRetrievalTool
    from src.tools.policy_retrieval_tool import PolicyRetrievalTool
    from src.tools.scope_policy_retrieval_tool import ScopePolicyRetrievalTool
    from src.utils.logger import get_logger
    from src.vector_store.index_manager import VectorIndexManager

    logger = get_logger("test_red_flag_question")

    settings = get_settings()
    auditor_id = (args.auditor or settings.auditor_id).strip()
    llm = AzureOpenAILLMProvider(settings).get_client()
    embeddings = AzureOpenAIEmbeddingProvider(settings)
    index_manager = VectorIndexManager(settings, embeddings)

    logger.info("[RedFlagTest] auditor=%s | engagement=%s | endpoint=%s",
                auditor_id, args.engagement, settings.databricks_vector_search_endpoint)

    hcbi_tool         = HcbiRetrievalTool(index_manager, settings, llm=llm)
    scope_policy_tool = ScopePolicyRetrievalTool(index_manager, settings)
    dd_policy_tool    = PolicyRetrievalTool(index_manager, settings, llm=llm)
    evidence_tool     = EvidenceRetrievalTool(index_manager, settings, auditor_folder=auditor_id, llm=llm)

    supervisor = build_audit_graph(
        llm=llm,
        scope_identifier_agent=ScopeIdentifierAgent(llm),
        hcbi_policy_agent=HcbiPolicyAgent(llm, hcbi_tool),
        policy_knowledge_agent=PolicyKnowledgeAgent(llm, scope_policy_tool, dd_policy_tool),
        supporting_docs_agent=SupportingDocsAgent(llm, evidence_tool),
        risk_evaluation_agent=RiskEvaluationAgent(llm),
        transactional_data_agent=TransactionalDataAgent(llm, settings),
        risk_classifier_agent=RiskClassifierAgent(llm),
        risk_writer_agent=RiskWriterAgent(llm),
        tpi_inventory_block="",
        transactional_context="",
        recurrence_context="",
        audit_context=settings.audit_scope_context,
    )

    writer = OutputWriter(OutputRepository(), settings)
    try:
        writer.set_valid_supporting_files(index_manager.list_document_names("supporting", auditor_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RedFlagTest] could not list supporting doc names | %s", exc)

    question = AuditQuestion(
        question_id="REDFLAG-1",
        question_text=_ITEM["question"],
        response_text=_ITEM["response"],
        followup_question=_ITEM["fq"],
        followup_response=_ITEM["fr"],
        scope_id=settings.current_scope_id,
        auditor_id=auditor_id,
        questionnaire_type=_ITEM["type"],
        source_table=_ITEM["type"],
    )

    logger.info("[RedFlagTest] Running supervisor on the Due Diligence red-flag question…")
    out = await supervisor.run(
        question_text=question.question_text,
        response_text=question.response_text,
        scope_id=question.scope_id,
        questionnaire_type=question.questionnaire_type,
        followup_question=question.followup_question,
        followup_response=question.followup_response,
    )

    state = AuditState(question=question)
    state.risk_finding.ai_recommendation = out.content
    state.risk_finding.calculation_evidence = out.calculation_evidence
    state.risk_finding.sql_queries = out.sql_queries
    state.risk_finding.supporting_doc_names = out.supporting_doc_names
    state.risk_finding.must_cite_doc_names = out.must_cite_docs
    state.processing_complete = True
    row = writer.row_for(state, sno=1)

    def _show(label, val):
        print(f"\n===== {label} =====\n{val if (val not in (None, '')) else '(empty)'}")

    print("\n" + "#" * 72 + "\nDUE DILIGENCE — RED FLAG QUESTION\n" + "#" * 72)
    print(f"Scope Area   : {row.scope_area}")
    print(f"Risk Title   : {row.risk_title}")
    print(f"Risk Type    : {row.risk_type}")
    _show("RISK DESCRIPTION", row.risk_description)
    _show("RISK RATIONALE", row.risk_rationale)
    _show("SOURCE DETAILS (← red-flag Excel files should appear here)", row.source_details)
    print(f"\n===== SUPERVISOR SURFACED SUPPORTING DOCS =====\n{out.supporting_doc_names or '(none)'}")

    # ── The actual cross-check: were the red-flag Excel evidence files pulled? ─────────────────────
    haystack = f"{row.source_details or ''}\n{out.supporting_doc_names or ''}".lower()
    print("\n" + "=" * 72 + "\nRED-FLAG EVIDENCE PULL CHECK\n" + "=" * 72)
    all_ok = True
    for doc in _EXPECTED_DOCS:
        hit = doc.lower() in haystack or doc.strip().lower() in haystack
        all_ok = all_ok and hit
        print(f"  [{'PULLED ' if hit else 'MISSING'}] {doc}")
    print("\nRESULT:", "✅ red-flag evidence pulled" if all_ok
          else "❌ at least one red-flag file NOT pulled (check the BM25 'lexical_matches' log line)")

    excel_path = Path(args.excel)
    if not excel_path.is_absolute():
        excel_path = Path.cwd() / excel_path
    if excel_path.exists():
        excel_path.unlink()
    try:
        writer.write_one(state, sno=1, output_path=excel_path)
        print(f"\nExcel written -> {excel_path}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RedFlagTest] could not write Excel | %s", exc)
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
