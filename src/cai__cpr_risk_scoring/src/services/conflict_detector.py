import logging
import uuid
from typing import Optional

import numpy as np
from langchain_openai import AzureOpenAIEmbeddings
from langgraph.store.memory import InMemoryStore

from src.config.settings import Settings
from src.models.schemas import AuditState, ConflictFlag, ConflictSeverity

logger = logging.getLogger(__name__)


class ConflictDetector:
    """
    Cross-row conflict detection using LangGraph InMemoryStore.
    Compares each question's response embedding against all previously processed
    responses in the same audit session. Flags pairs exceeding the similarity threshold.
    """

    def __init__(self, embeddings: AzureOpenAIEmbeddings, settings: Settings) -> None:
        self._embeddings = embeddings
        self._threshold = settings.conflict_similarity_threshold
        self._store = InMemoryStore()
        self._namespace = ("audit_responses",)

    def detect_and_flag(self, state: AuditState) -> AuditState:
        question = state.question
        text = f"{question.question_text}\n{question.response_text}"
        embedding = self._embeddings.embed_query(text)

        stored_items = self._store.search(self._namespace)
        conflicts: list[ConflictFlag] = []

        for item in stored_items:
            stored_question_id: str = item.key
            stored_embedding: list[float] = item.value["embedding"]
            stored_text: str = item.value["text"]

            similarity = self._cosine_similarity(embedding, stored_embedding)

            if similarity >= self._threshold and stored_question_id != question.question_id:
                severity = self._classify_severity(similarity)
                conflict = ConflictFlag(
                    conflict_id=str(uuid.uuid4()),
                    question_id_a=question.question_id,
                    question_id_b=stored_question_id,
                    similarity_score=float(similarity),
                    severity=severity,
                    description=(
                        f"Response to question {question.question_id} is highly similar "
                        f"(score={similarity:.3f}) to question {stored_question_id}, "
                        f"suggesting contradictory or duplicate information."
                    ),
                )
                conflicts.append(conflict)
                logger.warning(
                    "[ConflictDetector] Conflict detected: %s <-> %s (score=%.3f, severity=%s)",
                    question.question_id, stored_question_id, similarity, severity.value,
                )

        self._store.put(
            self._namespace,
            question.question_id,
            {"embedding": embedding, "text": text},
        )

        state.risk_finding.conflicts = conflicts
        return state

    def reset(self) -> None:
        """Clear stored embeddings — call between audit sessions."""
        self._store = InMemoryStore()

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        va, vb = np.array(a), np.array(b)
        norm_a, norm_b = np.linalg.norm(va), np.linalg.norm(vb)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        # Clamp to [0, 1]: floating-point rounding can push an identical/near-identical pair to
        # 1.0000000000000002, which would otherwise fail the ConflictFlag(similarity_score <= 1.0)
        # validator and crash the whole question into Manual Review.
        cosine = float(np.dot(va, vb) / (norm_a * norm_b))
        return max(0.0, min(1.0, cosine))

    @staticmethod
    def _classify_severity(score: float) -> ConflictSeverity:
        if score >= 0.97:
            return ConflictSeverity.CRITICAL
        if score >= 0.93:
            return ConflictSeverity.MODERATE
        return ConflictSeverity.MINOR
