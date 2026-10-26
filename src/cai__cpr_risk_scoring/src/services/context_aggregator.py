from src.models.schemas import AuditState, DocumentChunk, DocumentSource


_SOURCE_PRIORITY = {
    DocumentSource.HCBI: 0,
    DocumentSource.POLICY: 1,
    DocumentSource.SUPPORTING: 2,
}


class ContextAggregator:
    """
    Merges all retrieved chunks and applies the source hierarchy:
    HCBI Global Policy > Policy Documents > Supporting Evidence.
    Returns a single ordered, deduplicated context string for the LLM.
    """

    def aggregate(self, state: AuditState, max_chunks: int = 20) -> str:
        all_chunks: list[DocumentChunk] = (
            state.risk_finding.hcbi_chunks
            + state.risk_finding.policy_chunks
            + state.risk_finding.semantic_chunks
            + state.risk_finding.targeted_chunks
        )

        deduplicated = self._deduplicate(all_chunks)
        sorted_chunks = sorted(
            deduplicated,
            key=lambda c: (
                _SOURCE_PRIORITY.get(c.source, 3),
                -(c.similarity_score or 0.0),
            ),
        )
        top_chunks = sorted_chunks[:max_chunks]

        sections: dict[DocumentSource, list[str]] = {
            DocumentSource.HCBI: [],
            DocumentSource.POLICY: [],
            DocumentSource.SUPPORTING: [],
        }
        for chunk in top_chunks:
            sections[chunk.source].append(f"[{chunk.document_name}]\n{chunk.content}")

        parts: list[str] = []
        if sections[DocumentSource.HCBI]:
            parts.append("=== HCBI GLOBAL POLICY ===\n" + "\n\n---\n\n".join(sections[DocumentSource.HCBI]))
        if sections[DocumentSource.POLICY]:
            parts.append("=== POLICY DOCUMENTS ===\n" + "\n\n---\n\n".join(sections[DocumentSource.POLICY]))
        if sections[DocumentSource.SUPPORTING]:
            parts.append("=== SUPPORTING EVIDENCE ===\n" + "\n\n---\n\n".join(sections[DocumentSource.SUPPORTING]))

        return "\n\n".join(parts) or "No context retrieved."

    @staticmethod
    def _deduplicate(chunks: list[DocumentChunk]) -> list[DocumentChunk]:
        seen: set[str] = set()
        unique: list[DocumentChunk] = []
        for chunk in chunks:
            if chunk.chunk_id not in seen:
                seen.add(chunk.chunk_id)
                unique.append(chunk)
        return unique
