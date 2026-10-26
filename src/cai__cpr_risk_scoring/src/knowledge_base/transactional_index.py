"""Build indexable chunks from Phase-3 transactional findings.

Phase 3 produces one finding per scope area. These are persisted into the transactional-analysis
index (scoped to the auditor via the auditor_folder field) so Phase 2 can retrieve pre-identified
transactional risks as current-year context and cross-validate GL/amount claims against them.
"""

from src.models.schemas import DocumentChunk, DocumentSource
from src.schemas.state import RiskOutput

_CHUNK_PREFIX = "txn"


def _render(finding: RiskOutput) -> str:
    parts = [
        f"Scope Area: {finding.scope_area}",
        f"Risk Type: {finding.risk_type}",
        f"Risk Description: {finding.risk_description}",
    ]
    if finding.risk_rationale:
        parts.append(f"Rationale: {finding.risk_rationale}")
    if finding.calculation_evidence:
        parts.append(f"Calculation Evidence: {finding.calculation_evidence}")
    return "\n".join(parts)


def build_transactional_chunks(
    findings: list[RiskOutput], auditor_id: str
) -> list[DocumentChunk]:
    """Convert Phase-3 findings into auditor-scoped DocumentChunks for the transactional index."""
    chunks: list[DocumentChunk] = []
    for position, finding in enumerate(findings):
        content = _render(finding)
        chunks.append(
            DocumentChunk(
                chunk_id=f"{_CHUNK_PREFIX}::{auditor_id}::{position}",
                document_name=f"transactional_analysis::{finding.scope_area}",
                source=DocumentSource.TRANSACTIONAL_ANALYSIS,
                content=content,
                token_count=max(1, len(content.split())),
                scope_id=finding.scope_area,
                auditor_folder=auditor_id,
                metadata={
                    "risk_type": finding.risk_type,
                    "scope_area": finding.scope_area,
                    "risk_title": finding.risk_title.value,
                },
            )
        )
    return chunks
