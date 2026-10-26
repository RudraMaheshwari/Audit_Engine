import logging

import numpy as np
from langchain_openai import AzureOpenAIEmbeddings

logger = logging.getLogger(__name__)

SCOPE_AREAS: list[str] = [
    "TPI Due Diligence",
    "TPI Contracting",
    "TPI Onboarding",
    "TPI Monitoring",
    "TPI Termination",
    "HCP Engagement",
    "Speaker Programs",
    "Advisory Boards",
    "Consulting Agreements",
    "Educational Grants",
    "Charitable Contributions",
    "Sponsorships",
    "Travel and Entertainment",
    "Gifts and Hospitality",
    "Transparency Reporting",
    "Government Officials Interaction",
    "Import/Export Compliance",
    "Trade Sanctions",
    "Anti-Bribery and Corruption",
    "Anti-Kickback",
    "Promotional Materials Review",
    "Off-Label Promotion",
    "Medical Affairs Compliance",
    "Clinical Trial Compliance",
    "Investigator-Initiated Studies",
    "Data Privacy",
    "Information Security",
    "Records Management",
    "Conflict of Interest",
    "Insider Trading",
    "Antitrust and Competition",
    "Product Quality Complaints",
    "Pharmacovigilance",
    "Adverse Event Reporting",
    "Product Recall",
    "Environmental Health and Safety",
    "Occupational Safety",
    "Workplace Harassment",
    "Diversity and Inclusion",
    "Supplier Diversity",
    "Procurement Compliance",
    "Contract Manufacturing",
    "Distribution Compliance",
    "Reimbursement Compliance",
    "Pricing and Contracting",
    "Government Pricing",
    "Medicare and Medicaid Compliance",
    "Healthcare Reform Compliance",
]


class ScopeMapper:
    """
    Maps audit questions to one of the 48 scope areas using cosine similarity
    against pre-computed scope area embeddings.
    Embeddings are computed once at initialisation and cached.
    """

    def __init__(self, embeddings: AzureOpenAIEmbeddings) -> None:
        self._embeddings = embeddings
        self._scope_areas = SCOPE_AREAS
        self._scope_embeddings: list[list[float]] = []
        self._initialised = False

    def initialise(self) -> None:
        """Pre-compute and cache all 48 scope area embeddings."""
        logger.info("[ScopeMapper] Computing embeddings for %d scope areas", len(self._scope_areas))
        self._scope_embeddings = self._embeddings.embed_documents(self._scope_areas)
        self._initialised = True
        logger.info("[ScopeMapper] Scope embeddings ready")

    def map(self, question_text: str, response_text: str) -> tuple[str, float]:
        """
        Returns (scope_area_name, confidence_score) for the best matching scope.
        Raises RuntimeError if called before initialise().
        """
        if not self._initialised:
            raise RuntimeError("ScopeMapper.initialise() must be called before map()")

        query = f"{question_text}\n{response_text}"
        query_embedding = self._embeddings.embed_query(query)

        scores = [
            self._cosine_similarity(query_embedding, scope_emb)
            for scope_emb in self._scope_embeddings
        ]

        best_idx = int(np.argmax(scores))
        best_score = float(scores[best_idx])
        best_scope = self._scope_areas[best_idx]

        logger.debug("[ScopeMapper] Matched: %s (score=%.3f)", best_scope, best_score)
        return best_scope, best_score

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        va, vb = np.array(a), np.array(b)
        norm_a, norm_b = np.linalg.norm(va), np.linalg.norm(vb)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(va, vb) / (norm_a * norm_b))
