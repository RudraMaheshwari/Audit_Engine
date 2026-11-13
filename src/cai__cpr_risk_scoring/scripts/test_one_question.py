"""Run ONE questionnaire question through the full per-question pipeline and print the resulting
Output row — a focused harness to cross-check that supporting docs, source details, calc evidence,
SQL queries, and column mapping come through correctly (defaults to the IDR HCC Monitoring item, row 6).

It builds the SAME agents/tools/supervisor the real run uses (no ingestion — indexes must already
exist), runs the one question, then prints every Output column AND writes a one-row Excel so you can
eyeball the Calculation Evidence (Col J) and SQL Queries columns.

Run (venv active, .env set with AUDITOR_ID / engagement / scope period / endpoint as for a real run):
    python scripts/test_one_question.py                         # default: HCC Monitoring item (no flags)
    python scripts/test_one_question.py --auditor Naeem_7 --type planning
    python scripts/test_one_question.py --question "..." --response "..." --type idr
"""

from __future__ import annotations

import argparse
import asyncio
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

from src.agents.hcbi_policy_agent import HcbiPolicyAgent  # noqa: E402
from src.agents.policy_knowledge_agent import PolicyKnowledgeAgent  # noqa: E402
from src.agents.risk_classifier_agent import RiskClassifierAgent  # noqa: E402
from src.agents.risk_evaluation_agent import RiskEvaluationAgent  # noqa: E402
from src.agents.risk_writer_agent import RiskWriterAgent  # noqa: E402
from src.agents.scope_identifier_agent import ScopeIdentifierAgent  # noqa: E402
from src.agents.supporting_docs_agent import SupportingDocsAgent  # noqa: E402
from src.agents.transactional_data_agent import TransactionalDataAgent  # noqa: E402
from src.config.settings import get_settings  # noqa: E402
from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider  # noqa: E402
from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider  # noqa: E402
from src.models.schemas import AuditQuestion, AuditState  # noqa: E402
from src.output.output_writer import OutputWriter  # noqa: E402
from src.pipeline.graph import build_audit_graph  # noqa: E402
from src.repositories.output_repo import OutputRepository  # noqa: E402
from src.tools.evidence_retrieval_tool import EvidenceRetrievalTool  # noqa: E402
from src.tools.hcbi_retrieval_tool import HcbiRetrievalTool  # noqa: E402
from src.tools.policy_retrieval_tool import PolicyRetrievalTool  # noqa: E402
from src.tools.scope_policy_retrieval_tool import ScopePolicyRetrievalTool  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402
from src.vector_store.index_manager import VectorIndexManager  # noqa: E402

logger = get_logger("test_one_question")

# Default = BOTH questions that fed output row 6 ("HCC Monitoring"): the planning competency item
# [219] and the IDR HCC-monitoring item [292]. With no --question flag, the harness runs both and
# writes both rows to the Excel (so the aggregation inputs can be eyeballed together). Pass
# --question to run a single custom item instead.
_DEFAULT_ITEMS = [
    {
        "type": "planning",
        "question": ("What is your assessment of the competency level of the Compliance resources at "
                     "this Company?"),
        "fq": "",
        "response": ("Very experienced and competent in compliance matters. In the past 12 months, "
                     "the HCC Specialist was on maternity leave and while we had a contractor to help "
                     "us bridge the gap, the HCCO had to manage a significant workload. Consequently, "
                     "we had to prioritize and certain activities (e.g., monitoring) were intentionaly "
                     "pushed to 2026."),
        "fr": "",
    },
    {
        "type": "idr",
        "question": ("Please provide copies of your documented HCC Monitoring plans (including results "
                     "from monitoring activities) during our scope period."),
        "fq": ("Could you please share the monitoring report for our scope period, in line with the "
               "monitoring plan, if it has not already been provided?"),
        "response": ("Please refer to page 7 of the 2H 2025 Compliance Committee deck. Compliance deck "
                     "is attached related to Tab 4. No. 2"),
        "fr": ("Considering the past audit or GACS assessment, IM KR did not have any significant gap "
               "in process or controls. So, it was decided that there was no monitoring over the "
               "transactions in 2025. Instead, the data analysis was conducted to have the high level "
               "oversight for the activities of FFS and product presentation. The results were "
               "included in HCC Committee meeting decks"),
    },
]


async def _run() -> int:
    p = argparse.ArgumentParser(description="Run the row-6 questions through the pipeline and print the Output rows.")
    p.add_argument("--auditor", default=None, help="auditor_id (default: Settings.auditor_id)")
    p.add_argument("--question", default=None, help="run a SINGLE custom item instead of the two row-6 defaults")
    p.add_argument("--response", default="")
    p.add_argument("--followup-q", dest="fq", default="")
    p.add_argument("--followup-r", dest="fr", default="")
    p.add_argument("--type", dest="qtype", default="idr", help="questionnaire_type: planning | idr (single-item mode)")
    p.add_argument("--excel", default="test_one_question_output.xlsx",
                   help="path to write the Output rows (per-question sheet incl. Calc Evidence + SQL columns)")
    args = p.parse_args()

    # Single custom item when --question is given; otherwise both row-6 defaults.
    if args.question is not None:
        items = [{"type": args.qtype, "question": args.question, "fq": args.fq,
                  "response": args.response, "fr": args.fr}]
    else:
        items = _DEFAULT_ITEMS

    settings = get_settings()
    auditor_id = (args.auditor or settings.auditor_id).strip()
    llm = AzureOpenAILLMProvider(settings).get_client()
    embeddings = AzureOpenAIEmbeddingProvider(settings)
    index_manager = VectorIndexManager(settings, embeddings)

    logger.info("[Test] auditor=%s | items=%d | endpoint=%s | scope_period=%s",
                auditor_id, len(items), settings.databricks_vector_search_endpoint,
                settings.audit_scope_context or "(none)")

    # Tools + agents — same construction as runner.run_audit (no ingestion; indexes must exist).
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
        tpi_inventory_block="",            # not loaded for the harness (GATE B TPI recognition only)
        transactional_context="",
        recurrence_context="",
        audit_context=settings.audit_scope_context,
    )

    writer = OutputWriter(OutputRepository(), settings)
    try:
        writer.set_valid_supporting_files(index_manager.list_document_names("supporting", auditor_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Test] could not list supporting doc names | %s", exc)

    def _show(label, val):
        print(f"\n===== {label} =====\n{val if (val not in (None, '')) else '(empty)'}")

    # append_row APPENDS, so clear any prior file once; each item is then written as its own row.
    excel_path = Path(args.excel)
    if not excel_path.is_absolute():
        excel_path = Path.cwd() / excel_path
    excel_path.parent.mkdir(parents=True, exist_ok=True)
    if excel_path.exists():
        excel_path.unlink()

    for sno, item in enumerate(items, start=1):
        question = AuditQuestion(
            question_id=f"TEST-{sno}",
            question_text=item["question"],
            response_text=item["response"],
            followup_question=item["fq"],
            followup_response=item["fr"],
            scope_id=settings.current_scope_id,
            auditor_id=auditor_id,
            questionnaire_type=item["type"],
            source_table=item["type"],
        )

        logger.info("[Test] Running supervisor… item %d/%d | type=%s", sno, len(items), item["type"])
        out = await supervisor.run(
            question_text=question.question_text,
            response_text=question.response_text,
            scope_id=question.scope_id,
            questionnaire_type=question.questionnaire_type,
            followup_question=question.followup_question,
            followup_response=question.followup_response,
        )

        # Mirror the runner: stamp the supervisor output onto the state's risk_finding, then build the row.
        state = AuditState(question=question)
        state.risk_finding.ai_recommendation = out.content
        state.risk_finding.calculation_evidence = out.calculation_evidence
        state.risk_finding.sql_queries = out.sql_queries
        state.risk_finding.supporting_doc_names = out.supporting_doc_names
        state.risk_finding.must_cite_doc_names = out.must_cite_docs
        state.processing_complete = True

        row = writer.row_for(state, sno=sno)

        print("\n" + "#" * 70 + f"\nOUTPUT ROW {sno}/{len(items)}  (type={item['type']})\n" + "#" * 70)
        print(f"Question              : {item['question'][:100]}")
        print(f"Scope Area            : {row.scope_area}")
        print(f"Risk Title            : {row.risk_title}")
        print(f"Risk Type             : {row.risk_type}")
        print(f"Risk Category         : {row.risk_category}")
        print(f"Transactional invoked : {out.transactional_invoked}  "
              f"(if False, no per-question NL->SQL ran, so Calc/SQL below are expected empty)")
        _show("RISK DESCRIPTION", row.risk_description)
        _show("RISK RATIONALE", row.risk_rationale)
        _show("RISK CATEGORY REASONING", row.risk_category_reasoning)
        _show("SOURCE DETAILS (← check supporting docs + exact names here)", row.source_details)
        _show("CALCULATION EVIDENCE (← Col J; parsed from NL->SQL prose)", row.calculation_evidence)
        _show("SQL QUERIES (← exact executed SQL, captured deterministically)", row.sql_queries)
        print(f"\n===== SUPERVISOR SURFACED SUPPORTING DOCS =====\n{out.supporting_doc_names or '(none)'}")

        try:
            writer.write_one(state, sno=sno, output_path=excel_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Test] could not write row %d to Excel | %s", sno, exc)

    print(f"\n===== VALID (indexed) SUPPORTING FILES (sample) =====\n"
          f"{sorted(getattr(writer, '_valid_supporting_files', set()))[:20]}")
    print(f"\n===== EXCEL WRITTEN =====\n{excel_path}\n"
          f"{len(items)} row(s). Open it and check the 'Calculation Evidence' and 'SQL Queries' columns.")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
