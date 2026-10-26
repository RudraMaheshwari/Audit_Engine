"""Loads the prior-auditor Market Overview reference texts used as style/structure guidance.

These are not facts about the current audit; they teach the generator the expected wording, tone,
and structure. Supply them either as one file per auditor in the references directory (.txt / .md),
or via the inline fallback list.
"""

from __future__ import annotations

from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger(__name__)


class ReferenceLibrary:
    """Provides the reference Market Overview texts and their prompt-ready rendering."""

    _SUFFIXES = {".txt", ".md"}
    _INLINE_FALLBACK: list[str] = []

    def __init__(self, references_dir: Path) -> None:
        self._references_dir = references_dir

    def load(self) -> list[str]:
        """Return reference texts from files, falling back to the inline list."""
        from_files = self._load_from_files()
        if from_files:
            return from_files

        inline = [t.strip() for t in self._INLINE_FALLBACK if t and t.strip()]
        if inline:
            logger.info("[References] Using %d inline Market Overview reference(s)", len(inline))
            return inline

        logger.warning(
            "[References] No reference Market Overviews found in %s and no inline fallback. "
            "Generation will proceed with weaker style guidance.", self._references_dir,
        )
        return []

    def render_block(self) -> str:
        """Return the references formatted as a numbered block for the prompt."""
        texts = self.load()
        if not texts:
            return "(No prior Market Overview references were supplied.)"
        blocks = [f"--- REFERENCE MARKET OVERVIEW #{i} ---\n{text}"
                  for i, text in enumerate(texts, start=1)]
        return "\n\n".join(blocks)

    def _load_from_files(self) -> list[str]:
        if not self._references_dir.is_dir():
            return []
        texts: list[str] = []
        for path in sorted(self._references_dir.iterdir()):
            if path.is_file() and path.suffix.lower() in self._SUFFIXES:
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    texts.append(content)
        if texts:
            logger.info(
                "[References] Loaded %d Market Overview reference file(s) from %s",
                len(texts), self._references_dir,
            )
        return texts
