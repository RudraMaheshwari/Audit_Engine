"""Unified, strictly per-auditor retrieval over the knowledge-base indexes.

Every KB consumer goes through this one abstraction so scoping, caching, and top-k are enforced in a
single place. Every query is filtered to the current auditor (auditor_folder): past-audit, SI-History
& Legal, local policies, and transactional findings all belong to a single auditor and are never
shared across auditors. Results are cached per run so repeated lookups (same query/scope across
findings) are computed once.
"""

from src.models.schemas import DocumentChunk
from src.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_TOP_K = 5

_PAST_AUDIT_INDEX = "past_audit"
_SI_HISTORY_INDEX = "si_history"
_LEGAL_INDEX = "legal"
_ADDITIONAL_INPUT_INDEX = "additional_input"
_LOCAL_POLICIES_INDEX = "local_policies"
_TRANSACTIONAL_INDEX = "transactional_analysis"
_QUESTIONNAIRE_INDEX = "questionnaire"


class KnowledgeRetriever:
    """Per-auditor, cached retrieval over the KB indexes for one audit engagement."""

    def __init__(self, index_manager, auditor_id: str, top_k: int = _DEFAULT_TOP_K) -> None:
        self._index_manager = index_manager
        self._auditor_id = auditor_id
        self._top_k = top_k
        self._cache: dict[tuple, list[DocumentChunk]] = {}

    def recurrence_for(self, query: str, *, top_k: int | None = None) -> list[DocumentChunk]:
        """Past-audit + SI History + Legal + Additional Input hits for a recurrence check."""
        past = self._retrieve(_PAST_AUDIT_INDEX, query, top_k=top_k)
        si = self._retrieve(_SI_HISTORY_INDEX, query, top_k=top_k)
        legal = self._retrieve(_LEGAL_INDEX, query, top_k=top_k)
        additional = self._retrieve(_ADDITIONAL_INPUT_INDEX, query, top_k=top_k)
        return past + si + legal + additional

    def has_recurrence_history(self) -> bool:
        """True when at least one historical KB index (past audit / SI History / Legal / Additional
        Input) actually holds data for this auditor — so a 'No' recurrence is a SEARCHED conclusion, not
        merely absent data. When none are populated, recurrence cannot be determined for this auditor."""
        for index_key in (_PAST_AUDIT_INDEX, _SI_HISTORY_INDEX, _LEGAL_INDEX, _ADDITIONAL_INPUT_INDEX):
            try:
                if self._index_manager.is_populated_for_auditor(index_key, self._auditor_id):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def si_history_for(self, query: str, *, top_k: int | None = None) -> list[DocumentChunk]:
        """SI History-only hits."""
        return self._retrieve(_SI_HISTORY_INDEX, query, top_k=top_k)

    def legal_for(self, query: str, *, top_k: int | None = None) -> list[DocumentChunk]:
        """Legal-only hits."""
        return self._retrieve(_LEGAL_INDEX, query, top_k=top_k)

    def additional_input_for(self, query: str, *, top_k: int | None = None) -> list[DocumentChunk]:
        """Additional Input-only hits."""
        return self._retrieve(_ADDITIONAL_INPUT_INDEX, query, top_k=top_k)

    def past_audit_for(self, query: str, *, top_k: int | None = None) -> list[DocumentChunk]:
        """Past-audit-only hits."""
        return self._retrieve(_PAST_AUDIT_INDEX, query, top_k=top_k)

    def local_policy_for(self, query: str, *, top_k: int | None = None) -> list[DocumentChunk]:
        """Local (region-specific) policy hits for a query."""
        return self._retrieve(_LOCAL_POLICIES_INDEX, query, top_k=top_k)

    def transactional_for(
        self, query: str, *, scope_area: str | None = None, top_k: int | None = None
    ) -> list[DocumentChunk]:
        """Pre-identified transactional-analysis findings, optionally narrowed to one scope area."""
        extra = {"scope_id": scope_area} if scope_area else None
        return self._retrieve(_TRANSACTIONAL_INDEX, query, top_k=top_k, extra_filters=extra)

    def questionnaire_for(self, query: str, *, top_k: int | None = None) -> list[DocumentChunk]:
        """Management/questionnaire Q&A hits for a query (the shared questionnaire index).

        Uses the index manager's dedicated questionnaire query path (its own schema) and caches per run.
        """
        effective_top_k = top_k or self._top_k
        cache_key = (_QUESTIONNAIRE_INDEX, query, effective_top_k, ())
        if cache_key in self._cache:
            return self._cache[cache_key]
        try:
            results = self._index_manager.search_questionnaire(
                query=query, auditor_id=self._auditor_id, top_k=effective_top_k
            )
        except Exception as exc:
            logger.warning(
                "[KnowledgeRetriever] questionnaire retrieval failed — returning empty | error=%s", exc
            )
            results = []
        self._cache[cache_key] = results
        return results

    def _retrieve(
        self,
        index_key: str,
        query: str,
        *,
        top_k: int | None = None,
        extra_filters: dict | None = None,
    ) -> list[DocumentChunk]:
        effective_top_k = top_k or self._top_k
        extra_items = tuple(sorted((extra_filters or {}).items()))
        cache_key = (index_key, query, effective_top_k, extra_items)
        if cache_key in self._cache:
            return self._cache[cache_key]

        filters: dict = {"auditor_folder": self._auditor_id}
        if extra_filters:
            filters.update(extra_filters)

        try:
            results = self._index_manager.similarity_search(
                index_key=index_key,
                query=query,
                scope_id=None,
                top_k=effective_top_k,
                filters=filters,
            )
        except Exception as exc:
            logger.warning(
                "[KnowledgeRetriever] retrieval failed — returning empty | index=%s | error=%s",
                index_key, exc,
            )
            results = []

        self._cache[cache_key] = results
        return results
