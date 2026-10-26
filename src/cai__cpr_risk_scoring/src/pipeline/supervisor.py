import asyncio
import json
import re
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError

from src.agents.hcbi_policy_agent import HcbiPolicyAgent
from src.agents.policy_knowledge_agent import PolicyKnowledgeAgent
from src.agents.risk_classifier_agent import RiskClassifierAgent
from src.agents.risk_evaluation_agent import RiskEvaluationAgent
from src.agents.risk_writer_agent import RiskWriterAgent
from src.agents.scope_identifier_agent import ScopeIdentifierAgent
from src.agents.supporting_docs_agent import SupportingDocsAgent
from src.agents.transactional_data_agent import TransactionalDataAgent
from src.config.constants import (
    AGENT_HCBI_POLICY,
    AGENT_POLICY_KNOWLEDGE,
    AGENT_RISK_CLASSIFIER,
    AGENT_RISK_EVALUATION,
    AGENT_RISK_WRITER,
    AGENT_SCOPE_IDENTIFIER,
    AGENT_SUPPORTING_DOCS,
    AGENT_TRANSACTIONAL,
    MAX_TRANS_RETRIES,
    RISK_CATEGORY_NONE,
    RISK_CATEGORY_NOT_CATEGORIZED,
    SCOPE_CONFIDENCE_HIGH,
    SCOPE_CONFIDENCE_MODERATE,
    SCOPE_ID_TO_DISPLAY_NAME,
    SCOPE_UNCLEAR_LABEL,
    SCOPE_UNFILTERED_LABEL,
)
from src.observability.tracing import observe
from src.scoring import is_further_review, reconcile_category
from src.scoring.risk_matrix import band_confidence
from src.utils.logger import get_logger


@dataclass
class SupervisorResult:
    """Structured output of one question's pipeline run.

    `.content` is the RiskWriter text the output writer parses for the Excel row; the remaining
    fields are the analysis metrics captured for observability (obs_question), so the runner does
    not have to re-parse the free text or fall back to the input scope placeholder.
    """
    content: str
    assigned_scope: str | None = None
    risk_category: str | None = None
    matrix_score: int | None = None
    likelihood: int | None = None
    impact: int | None = None
    llm_confidence: float | None = None
    derived_confidence: float | None = None
    transactional_invoked: bool = False
    calculation_evidence: str = ""

# Prompt-registry keys per agent (used to stamp prompt_name/version/hash onto each LLM call).
_PROMPT_HCBI          = "hcbi_retrieval"
_PROMPT_POLICY        = "policy_knowledge"
_PROMPT_SUPPORTING    = "supporting_docs"
_PROMPT_RISK_EVAL     = "risk_evaluation"
_PROMPT_TRANSACTIONAL = "transactional_data"
_PROMPT_CLASSIFIER    = "risk_classifier"
_PROMPT_SCOPE         = "scope_identifier"
_PROMPT_WRITER        = "risk_writer"

logger = get_logger(__name__)

_QUANT_SIGNAL = re.compile(
    r"[$€£¥]"
    r"|\b(?:AUD|USD|NZD|EUR|GBP|CAD)\b"
    r"|\d\s?%"
    r"|\d[\d,]*\.\d+"
    r"|\d{1,3}(?:,\d{3})+"
    r"|\b\d{4,}\b"
    r"|\b\d+(?:\.\d+)?\s?(?:million|billion|thousand|k|m|bn)\b",
    re.IGNORECASE,
)


class AuditSupervisor:
    """
    Orchestrates the FCPA audit pipeline for a single question (scope-after-analysis):
      Phase A  — HcbiPolicyAgent, PolicyKnowledgeAgent, SupportingDocsAgent run in parallel,
                 with UNFILTERED retrieval (scope is not used to pre-filter).
      Phase B  — RiskEvaluationAgent → (optional) TransactionalDataAgent → RiskClassifierAgent.
      Scope   — ScopeIdentifierAgent categorises the ALREADY-identified risk into FCPA scope
                area(s) AFTER analysis (multi-scope only when confident).
      Writer  — RiskWriterAgent writes the final risk using the assigned scope(s).
    Returns the structured risk-writer output string for each question.
    """

    def __init__(
        self,
        llm: BaseChatModel,
        scope_identifier_agent: ScopeIdentifierAgent,
        hcbi_policy_agent: HcbiPolicyAgent,
        policy_knowledge_agent: PolicyKnowledgeAgent,
        supporting_docs_agent: SupportingDocsAgent,
        risk_evaluation_agent: RiskEvaluationAgent,
        transactional_data_agent: TransactionalDataAgent,
        risk_classifier_agent: RiskClassifierAgent,
        risk_writer_agent: RiskWriterAgent,
        tpi_inventory_block: str = "",
        transactional_context: str = "",
        recurrence_context: str = "",
    ) -> None:
        self._tpi_inventory_block = tpi_inventory_block
        self._transactional_context = transactional_context
        self._recurrence_context = recurrence_context
        self._scope_id    = scope_identifier_agent.get_agent()
        self._hcbi        = hcbi_policy_agent.get_agent()
        self._policy      = policy_knowledge_agent.get_agent()
        self._supporting  = supporting_docs_agent.get_agent()
        self._risk_eval   = risk_evaluation_agent.get_agent()
        self._transact    = transactional_data_agent.get_agent()
        self._risk_anal   = risk_classifier_agent.get_agent()
        self._risk_writer = risk_writer_agent.get_agent()
        logger.info(
            "[AuditSupervisor] Compiled | mode=scope-after-analysis | "
            "phase_a=parallel(hcbi+policy+supporting · unfiltered) | "
            "phase_b=sequential(eval→transact?→classifier) | "
            "scope=post-analysis categorisation | writer=uses assigned scope"
        )

    # Cap each message-based ReAct agent's tool-calling loop. LangGraph counts super-steps (~2 per
    # model+tool cycle), so 8 allows ~3-4 retrieval rounds then an answer — enough for normal
    # retrieval, but it stops the runaway loops that pushed some questions to 38-40 LLM calls.
    # This applies ONLY to the 7 message agents invoked here; the transactional/NL-to-SQL agent runs
    # through a different path with its own max_iterations and is intentionally left uncapped.
    AGENT_RECURSION_LIMIT = 8

    @staticmethod
    async def _invoke_agent(agent, agent_name: str, prompt_name: str, msg: str):
        """Invoke a message-based agent, tagging the LLM call with its agent + prompt for tracing."""
        with observe(agent_name=agent_name, prompt_name=prompt_name):
            try:
                return await agent.ainvoke(
                    {"messages": [HumanMessage(content=msg)]},
                    config={"recursion_limit": AuditSupervisor.AGENT_RECURSION_LIMIT},
                )
            except GraphRecursionError:
                logger.warning(
                    "[AuditSupervisor] %s hit recursion cap (%d) — using best-effort output",
                    agent_name, AuditSupervisor.AGENT_RECURSION_LIMIT,
                )
                return {"messages": [AIMessage(content="")]}

    async def run(
        self,
        question_text: str,
        response_text: str,
        scope_id: str,
        questionnaire_type: str = "planning",
        followup_question: str = "",
        followup_response: str = "",
    ) -> "SupervisorResult":
        """
        Run the full 8-agent pipeline for one audit question.
        Returns a SupervisorResult: the RiskWriter output string (.content) plus the analysis
        metrics (assigned scope, matrix axes/score, category, confidences, transactional flag)
        captured for observability.
        """
        response_label = (
            "TPI RESPONSE"
            if questionnaire_type == "planning"
            else "JNJ MANAGEMENT RESPONSE"
        )
        parts = [
            f"AUDIT QUESTION: {question_text}",
            f"{response_label}: {response_text}",
        ]
        if followup_question:
            parts.append(f"FOLLOW-UP QUESTION: {followup_question}")
        if followup_response:
            parts.append(f"FOLLOW-UP RESPONSE: {followup_response}")
        base_msg = "\n\n".join(parts)

        logger.info(
            "[AuditSupervisor] Running | scope=%s | type=%s | question='%s'",
            scope_id, questionnaire_type, question_text[:80],
        )

        # ── Phase A — retrieval is UNFILTERED. Scope is assigned AFTER analysis and is
        #    never used to pre-filter retrieval. Inject ANY_SCOPE so the scope-policy tool
        #    searches all scope areas by semantic relevance (scope-agnostic analysis).
        retrieval_msg = f"{base_msg}\n\nIDENTIFIED FCPA SCOPE(S): {SCOPE_UNFILTERED_LABEL}"

        hcbi_out, policy_out, supporting_out = await asyncio.gather(
            self._invoke_agent(self._hcbi, AGENT_HCBI_POLICY, _PROMPT_HCBI, retrieval_msg),
            self._invoke_agent(self._policy, AGENT_POLICY_KNOWLEDGE, _PROMPT_POLICY, retrieval_msg),
            self._invoke_agent(self._supporting, AGENT_SUPPORTING_DOCS, _PROMPT_SUPPORTING, retrieval_msg),
        )

        hcbi_ctx       = hcbi_out["messages"][-1].content
        policy_ctx     = policy_out["messages"][-1].content
        supporting_ctx = supporting_out["messages"][-1].content

        # ── Phase B — analysis is scope-agnostic (no scope label injected)
        eval_msg = (
            f"{base_msg}\n\n"
            f"=== HCBI POLICY CONTEXT ===\n{hcbi_ctx}\n\n"
            f"=== POLICY DOCUMENT CONTEXT ===\n{policy_ctx}\n\n"
            f"=== SUPPORTING EVIDENCE ===\n{supporting_ctx}"
        )
        if self._transactional_context:
            eval_msg += f"\n\n{self._transactional_context}"
        if self._recurrence_context:
            eval_msg += f"\n\n{self._recurrence_context}"
        risk_eval_out = await self._invoke_agent(
            self._risk_eval, AGENT_RISK_EVALUATION, _PROMPT_RISK_EVAL, eval_msg
        )
        risk_eval_ctx = risk_eval_out["messages"][-1].content

        transactional_ctx = ""
        run_transactional = self._is_transactional(risk_eval_ctx)
        if run_transactional and not self._has_quantitative_signal(response_text, followup_response):
            logger.info(
                "[AuditSupervisor] Transactional flag set but response has no numeric/currency "
                "signal — skipping transactional agent (deterministic guard)"
            )
            run_transactional = False
        if run_transactional:
            logger.info("[AuditSupervisor] Transactional intent detected — running transactional agent")
            trans_input = (
                f"{base_msg}\n\n"
                f"=== RISK EVALUATION ===\n{risk_eval_ctx}\n\n"
                "Query the transactional tables to validate the financial claims or "
                "quantitative statements in the response above."
            )
            for _attempt in range(1, MAX_TRANS_RETRIES + 1):
                try:
                    with observe(agent_name=AGENT_TRANSACTIONAL, prompt_name=_PROMPT_TRANSACTIONAL):
                        trans_out = await self._transact.ainvoke({"input": trans_input})
                    transactional_ctx = trans_out.get("output", "")
                    # LangChain returns a sentinel like "Agent stopped due to max iterations." when the
                    # SQL agent exhausts its step budget — replace that with a clean, professional line.
                    if transactional_ctx and "stopped due to" in transactional_ctx.lower():
                        transactional_ctx = "No suitable data found in the transactional dashboards to validate this response."
                    if transactional_ctx:
                        break
                except Exception as exc:
                    logger.warning(
                        "[AuditSupervisor] Transactional agent attempt %d/%d failed | error=%s",
                        _attempt, MAX_TRANS_RETRIES, exc,
                    )
                    transactional_ctx = ""
            if not transactional_ctx:
                logger.error(
                    "[AuditSupervisor] Transactional agent failed after %d attempts — "
                    "treating as No Risk for transactional component",
                    MAX_TRANS_RETRIES,
                )

        classifier_msg = f"{eval_msg}\n\n=== RISK EVALUATION ===\n{risk_eval_ctx}"
        if transactional_ctx:
            classifier_msg += f"\n\n=== TRANSACTIONAL DATA ===\n{transactional_ctx}"
        # Inject the auditor's TPI inventory for GATE B same-party recognition. It flows into the
        # writer too (writer_msg wraps classifier_msg).
        if self._tpi_inventory_block:
            classifier_msg += f"\n\n{self._tpi_inventory_block}"

        classifier_out = await self._invoke_agent(
            self._risk_anal, AGENT_RISK_CLASSIFIER, _PROMPT_CLASSIFIER, classifier_msg
        )
        classification_ctx = classifier_out["messages"][-1].content

        classification_ctx, matrix_score = reconcile_category(classification_ctx)
        if matrix_score is not None:
            logger.info(
                "[AuditSupervisor] Matrix | likelihood=%d | impact=%d | score=%d | category=%s",
                matrix_score.likelihood, matrix_score.impact,
                matrix_score.score, matrix_score.category,
            )

        logger.info(
            "[AuditSupervisor] Classification | result='%s'",
            classification_ctx[:120].replace("\n", " "),
        )

        # ── Scope categorisation AFTER analysis — assign FCPA scope(s) to the identified
        #    risk (never before/for retrieval). Multi-scope only when the agent is confident.
        assigned_scopes, scope_conf = await self._assign_scope_after_analysis(
            base_msg, risk_eval_ctx, classification_ctx
        )
        scope_label = self._format_assigned_scopes(assigned_scopes, scope_conf)
        logger.info(
            "[AuditSupervisor] Scope assigned (post-analysis) | scopes=%s | confidence=%.2f",
            scope_label, scope_conf,
        )

        writer_msg = (
            f"{classifier_msg}\n\n"
            f"=== CLASSIFICATION DECISION ===\n{classification_ctx}\n\n"
            f"ASSIGNED FCPA SCOPE(S): {scope_label}"
        )
        final_out = await self._invoke_agent(
            self._risk_writer, AGENT_RISK_WRITER, _PROMPT_WRITER, writer_msg
        )
        final_content = final_out["messages"][-1].content

        logger.info(
            "[AuditSupervisor] Done | scope=%s | response_length=%d",
            scope_label, len(final_content),
        )
        return SupervisorResult(
            content=final_content,
            assigned_scope=scope_label,
            risk_category=(
                matrix_score.category if matrix_score
                else (
                    RISK_CATEGORY_NOT_CATEGORIZED
                    if is_further_review(classification_ctx)
                    else RISK_CATEGORY_NONE
                )
            ),
            matrix_score=(matrix_score.score if matrix_score else None),
            likelihood=(matrix_score.likelihood if matrix_score else None),
            impact=(matrix_score.impact if matrix_score else None),
            llm_confidence=scope_conf,
            derived_confidence=(band_confidence(matrix_score.score) if matrix_score else None),
            transactional_invoked=run_transactional,
            calculation_evidence=transactional_ctx,
        )

    _CONFIDENCE_HIGH     = SCOPE_CONFIDENCE_HIGH      # additional scopes assigned only at/above this
    _CONFIDENCE_MODERATE = SCOPE_CONFIDENCE_MODERATE  # below this → scope unclear (manual review)

    async def _assign_scope_after_analysis(
        self, base_msg: str, risk_eval_ctx: str, classification_ctx: str
    ) -> tuple[list[str], float]:
        """Categorise the ALREADY-identified risk into FCPA scope area(s).

        Runs AFTER the risk analysis — scope is never used to filter retrieval. Feeds the
        question/response plus the risk evaluation and classification into ScopeIdentifierAgent
        and returns (scopes, confidence): primary first, then any additional scopes. Retries
        once if the agent returns no scope.
        """
        scope_input = (
            f"{base_msg}\n\n"
            f"=== RISK EVALUATION ===\n{risk_eval_ctx}\n\n"
            f"=== CLASSIFICATION DECISION ===\n{classification_ctx}\n\n"
            "Categorise the identified risk above into the applicable FCPA scope area(s)."
        )
        for attempt in range(1, 3):
            out = await self._invoke_agent(
                self._scope_id, AGENT_SCOPE_IDENTIFIER, _PROMPT_SCOPE, scope_input
            )
            content = out["messages"][-1].content
            scopes, confidence = self._parse_scope_result(content)
            logger.info(
                "[AuditSupervisor] Post-analysis scope attempt=%d | primary=%s | "
                "additional=%s | confidence=%.2f",
                attempt,
                scopes[0] if scopes else "none",
                ", ".join(scopes[1:]) if len(scopes) > 1 else "none",
                confidence,
            )
            if scopes:
                return scopes, confidence
            if attempt < 2:
                logger.warning(
                    "[AuditSupervisor] No scope returned — retrying scope categorisation"
                )
        return [], 0.0

    def _format_assigned_scopes(self, scopes: list[str], confidence: float) -> str:
        """Build the Scope Area label for the identified risk.

        - No scope determined, or confidence below the moderate floor → the scope is not
          reliable, so flag it for a human: "Not Applicable".
        - Otherwise keep the primary scope; append additional scopes ONLY when the agent is
          confident (>= HIGH). Never emits the internal ANY_SCOPE sentinel.
        """
        if not scopes or confidence < self._CONFIDENCE_MODERATE:
            return SCOPE_UNCLEAR_LABEL
        display = [SCOPE_ID_TO_DISPLAY_NAME.get(s, s) for s in scopes]
        primary, additional = display[0], display[1:]
        if additional and confidence >= self._CONFIDENCE_HIGH:
            return "; ".join([primary] + additional)
        return primary

    @staticmethod
    def _parse_scope_result(scope_output: str) -> tuple[list[str], float]:
        scope_ids: list[str] = []
        primary = re.search(r"^Primary Scope:\s*(.+)$", scope_output, re.MULTILINE)
        if primary:
            sid = primary.group(1).strip()
            if sid and sid.upper() != "NONE":
                scope_ids.append(sid)
        additional = re.search(r"^Additional Scopes:\s*(.+)$", scope_output, re.MULTILINE)
        if additional:
            raw = additional.group(1).strip()
            if raw.upper() != "NONE":
                for sid in raw.split(","):
                    sid = sid.strip()
                    if sid:
                        scope_ids.append(sid)
        confidence = 0.0
        conf_match = re.search(r"^Confidence:\s*([0-9]*\.?[0-9]+)", scope_output, re.MULTILINE)
        if conf_match:
            try:
                confidence = float(conf_match.group(1))
                confidence = max(0.0, min(1.0, confidence))
            except ValueError:
                pass
        return scope_ids, confidence

    @staticmethod
    def _has_quantitative_signal(*texts: str) -> bool:
        blob = " ".join(t for t in texts if t)
        blob = re.sub(r"\b(?:19|20)\d{2}\b", " ", blob)
        return bool(_QUANT_SIGNAL.search(blob))

    @staticmethod
    def _is_transactional(risk_eval_content: str) -> bool:
        try:
            data = json.loads(risk_eval_content)
            return bool(data.get("transactional_intent_detected", False))
        except Exception:
            pass
        return bool(re.search(
            r"transactional_intent_detected[\"'\s:]+true",
            risk_eval_content,
            re.IGNORECASE,
        ))
