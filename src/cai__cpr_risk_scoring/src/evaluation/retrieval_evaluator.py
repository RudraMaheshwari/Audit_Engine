"""
Offline retrieval evaluation utilities.

precision_at_k  — fraction of retrieved chunks that are relevant
recall_at_k     — fraction of relevant chunks that were retrieved

Both require ground truth: a set of chunk_ids known to be relevant for a given query.
These are not called in the live pipeline — use them in evaluation notebooks or test harnesses.

Example usage:
    retrieved = [c.chunk_id for c in chunks]
    relevant  = {"chunk_abc", "chunk_def"}
    p = precision_at_k(retrieved, relevant, k=5)
    r = recall_at_k(retrieved, relevant, k=5)
"""

from __future__ import annotations


def precision_at_k(
    retrieved_ids: list[str],
    relevant_ids: set[str],
    k: int,
) -> float:
    """
    Precision@k = |relevant ∩ top-k retrieved| / k

    Args:
        retrieved_ids: Ordered list of retrieved chunk IDs (best first).
        relevant_ids:  Ground-truth set of relevant chunk IDs for this query.
        k:             Cutoff rank.

    Returns:
        Float in [0.0, 1.0]. Returns 0.0 if k <= 0 or retrieved_ids is empty.
    """
    if k <= 0 or not retrieved_ids:
        return 0.0
    top_k = retrieved_ids[:k]
    hits = sum(1 for cid in top_k if cid in relevant_ids)
    return hits / k


def recall_at_k(
    retrieved_ids: list[str],
    relevant_ids: set[str],
    k: int,
) -> float:
    """
    Recall@k = |relevant ∩ top-k retrieved| / |relevant|

    Args:
        retrieved_ids: Ordered list of retrieved chunk IDs (best first).
        relevant_ids:  Ground-truth set of relevant chunk IDs for this query.
        k:             Cutoff rank.

    Returns:
        Float in [0.0, 1.0]. Returns 0.0 if relevant_ids is empty or k <= 0.
    """
    if k <= 0 or not relevant_ids:
        return 0.0
    top_k = retrieved_ids[:k]
    hits = sum(1 for cid in top_k if cid in relevant_ids)
    return hits / len(relevant_ids)
