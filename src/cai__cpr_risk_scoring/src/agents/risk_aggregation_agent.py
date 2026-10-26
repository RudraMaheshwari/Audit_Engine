"""Risk Aggregation Agent — clusters raw findings into unique, matrix-scored risks.

The LLM groups findings into unique risks and assigns Likelihood/Impact for each cluster; this agent
then applies the deterministic Risk Matrix to those axes (so the category can never be mis-banded)
and resolves each cluster's source_refs back to the originating questions/transactions.
"""

import json

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.config.constants import AGENT_RISK_AGGREGATION
from src.prompts.risk_aggregation_prompt import (
    AGGREGATION_SYSTEM_PROMPT,
    AGGREGATION_USER_TEMPLATE,
)
from src.schemas.aggregation import (
    SOURCE_TYPE_QUESTIONNAIRE,
    SOURCE_TYPE_TRANSACTIONAL,
    AggregatedRisk,
    AggregatedRiskSource,
    RawFinding,
)
from src.scoring import band_confidence, score_risk
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Divider between successive blocks in the bucketed Output C columns (Questions & Responses,
# Calculation Evidence) so multiple clustered entries are visually distinguishable.
_BLOCK_SEPARATOR = "\n" + "─" * 60 + "\n"


class RiskAggregationAgent:
    """Clusters raw findings into unique risks, one per risk type, matrix-scored."""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm
        logger.info("[%s] Compiled | role=aggregation | tools=0", AGENT_RISK_AGGREGATION)

    def aggregate(
        self, raw_findings: list[RawFinding], recurrence_context: str = ""
    ) -> list[AggregatedRisk]:
        """Cluster raw findings into matrix-scored AggregatedRisks."""
        if not raw_findings:
            return []

        by_ref = {rf.source_ref: rf for rf in raw_findings}
        user_msg = AGGREGATION_USER_TEMPLATE.format(
            findings_block=self._render_findings(raw_findings),
            recurrence_block=(f"\n{recurrence_context}\n" if recurrence_context else ""),
        )

        logger.info(
            "[%s] Invoking LLM to cluster %d raw findings…",
            AGENT_RISK_AGGREGATION, len(raw_findings),
        )
        try:
            response = self._llm.invoke([
                SystemMessage(content=AGGREGATION_SYSTEM_PROMPT),
                HumanMessage(content=user_msg),
            ])
            clusters = self._parse(response.content)
        except Exception as exc:
            logger.error("[%s] Aggregation call/parse failed | error=%s", AGENT_RISK_AGGREGATION, exc)
            return []

        risks = [self._to_risk(c, by_ref) for c in clusters]
        risks = [r for r in risks if r is not None]
        logger.info(
            "[%s] Clustered %d raw findings into %d unique risk(s)",
            AGENT_RISK_AGGREGATION, len(raw_findings), len(risks),
        )
        return risks

    @staticmethod
    def _render_findings(raw_findings: list[RawFinding]) -> str:
        lines = []
        for rf in raw_findings:
            category = rf.risk_category or "n/a"
            lines.append(
                f"[{rf.source_ref}] {rf.scope_area or 'unknown'} | {category} | {rf.description}"
            )
        return "\n".join(lines)

    @staticmethod
    def _parse(content: str) -> list[dict]:
        raw = (content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = json.loads(raw)
        clusters = parsed.get("risks", []) if isinstance(parsed, dict) else []
        return [c for c in clusters if isinstance(c, dict)]

    def _to_risk(self, cluster: dict, by_ref: dict[str, RawFinding]) -> AggregatedRisk | None:
        clustered = [by_ref[ref] for ref in cluster.get("source_refs", []) if ref in by_ref]

        # A single-source TRANSACTIONAL cluster keeps its OWN data-informed band (the transactional
        # agent scored it against the actual rows) — so a standalone banded FRR stays exactly as scored
        # ("Medium stays Medium"). Every other cluster is scored on the LLM's cluster-level axes.
        if (
            len(clustered) == 1
            and clustered[0].source_type == SOURCE_TYPE_TRANSACTIONAL
            and clustered[0].likelihood >= 1
            and clustered[0].impact >= 1
        ):
            likelihood, impact = clustered[0].likelihood, clustered[0].impact
        else:
            try:
                likelihood = int(cluster.get("likelihood", 0))
                impact = int(cluster.get("impact", 0))
            except (TypeError, ValueError):
                likelihood, impact = 0, 0
        if likelihood < 1 or impact < 1:
            return None

        score = score_risk(impact, likelihood)
        llm_confidence = self._parse_confidence(cluster.get("confidence"))
        sources = [
            AggregatedRiskSource(
                source_ref=rf.source_ref,
                source_type=rf.source_type,
                source_table=rf.source_table,
                question=rf.question,
                response=rf.response,
                followup_question=rf.followup_question,
                followup_response=rf.followup_response,
            )
            for rf in clustered
        ]

        return AggregatedRisk(
            scope_area=(cluster.get("scope_area") or "").strip(),
            risk_type=(cluster.get("risk_type") or "").strip(),
            risk_description=(cluster.get("risk_description") or "").strip(),
            risk_rationale=(cluster.get("risk_rationale") or "").strip(),
            risk_category=score.category,
            likelihood=score.likelihood,
            impact=score.impact,
            matrix_score=score.score,
            llm_confidence=llm_confidence,
            derived_confidence=band_confidence(score.score),
            calculation_evidence=self._collect_calculation_evidence(clustered),
            sql_queries=self._collect_sql_queries(clustered),
            questionnaire_sources=self._bucket_questionnaire_sources(clustered),
            source_details=self._bucket_source_details(clustered),
            questions_and_responses=self._bucket_questions_and_responses(clustered),
            sources=sources,
        )

    @staticmethod
    def _parse_confidence(value) -> float:
        """Clamp the LLM's stated confidence into 0.0–1.0; default 0.0 when absent/unparseable."""
        try:
            return round(max(0.0, min(1.0, float(value))), 3)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _collect_calculation_evidence(clustered: list[RawFinding]) -> str:
        """Join the validating calculation evidence from any transactional findings in the cluster,
        carried over from the Phase-3 output so a bucketed transactional risk keeps its proof."""
        blocks = [
            rf.calculation_evidence.strip()
            for rf in clustered
            if rf.calculation_evidence and rf.calculation_evidence.strip()
        ]
        return _BLOCK_SEPARATOR.join(blocks)

    @staticmethod
    def _collect_sql_queries(clustered: list[RawFinding]) -> str:
        """Join the executed SQL queries from any transactional findings in the cluster so a user can
        re-run them on Databricks to reproduce the calculation evidence."""
        blocks = [
            rf.sql_queries.strip()
            for rf in clustered
            if rf.sql_queries and rf.sql_queries.strip()
        ]
        return _BLOCK_SEPARATOR.join(blocks)

    @staticmethod
    def _bucket_questionnaire_sources(clustered: list[RawFinding]) -> str:
        """Source group 1 — the distinct questionnaire files/tables the findings came from."""
        seen: list[str] = []
        for rf in clustered:
            if rf.source_type == SOURCE_TYPE_QUESTIONNAIRE and rf.source_table:
                if rf.source_table not in seen:
                    seen.append(rf.source_table)
        return ", ".join(seen)

    @staticmethod
    def _drop_unresolved_filename_entries(detail: str) -> str:
        """Remove citation entries whose File Name is 'Not Available' (unresolved sources)."""
        kept = [
            line for line in detail.splitlines()
            if "file name: not available" not in line.lower()
        ]
        return "\n".join(line for line in kept if line.strip())

    @staticmethod
    def _bucket_source_details(clustered: list[RawFinding]) -> str:
        """Source group 2 — the relevant retrieved chunks / citations across all findings."""
        parts: list[str] = []
        for rf in clustered:
            detail = RiskAggregationAgent._drop_unresolved_filename_entries(
                (rf.source_details or "").strip()
            )
            if detail and detail not in parts:
                parts.append(detail)
        return "\n".join(parts)

    @staticmethod
    def _bucket_questions_and_responses(clustered: list[RawFinding]) -> str:
        """Source group 3 — all questions, follow-ups, and responses across the cluster."""
        blocks: list[str] = []
        for rf in clustered:
            if rf.source_type != SOURCE_TYPE_QUESTIONNAIRE:
                continue
            header = f"[{rf.source_ref}] {rf.source_table}".strip()
            segment = [header]
            if rf.question:
                segment.append(f"Q: {rf.question}")
            if rf.followup_question:
                segment.append(f"Follow-up Q: {rf.followup_question}")
            if rf.response:
                segment.append(f"Response: {rf.response}")
            if rf.followup_response:
                segment.append(f"Follow-up Response: {rf.followup_response}")
            blocks.append("\n".join(segment))
        return _BLOCK_SEPARATOR.join(blocks)
