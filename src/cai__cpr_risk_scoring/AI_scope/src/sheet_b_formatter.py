"""Build the Sheet B 'Summary of Risks' cell: AI-structured system risk points + Additional Inputs.

Output shape per scope area:

    • <actual risk point 1>
    • <actual risk point 2>
    ...

    Additional Inputs:
    SI History - <risk types SI raised>
    Legal Inputs - <risk types Legal raised>
    Additional Input - <risk types Additional Input raised>
    Management Inputs - <risk types Management raised>
    Past Audit Observations - <risk types Past Audit raised>

The actual-risk dotted points are structured by the LLM from the system summary. The Additional
Inputs lines are composed deterministically from the Direction-2 source rows (verbatim — never sent
through the LLM) so no source wording is invented or altered. Best-effort: if the LLM is unavailable
the raw system summary is used as-is.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Source label (AIScopeRow.source) -> the line label used under "Additional Inputs".
_SOURCE_LINE_LABELS: list[tuple[str, str]] = [
    ("SI History", "SI History"),
    ("Legal", "Legal Inputs"),
    ("Additional Input", "Additional Input"),
    ("Management", "Management Inputs"),
    ("Past Audit", "Past Audit Observations"),
]


@dataclass
class ScopeSummaryParts:
    """Per-scope buckets of summary text, split by row source."""

    system: list[str] = field(default_factory=list)
    by_source: dict[str, list[str]] = field(default_factory=dict)

    def add(self, source: str, text: str) -> None:
        if not text:
            return
        if source == "System":
            self.system.append(text)
        else:
            self.by_source.setdefault(source, []).append(text)


# A natural story lead per source ({items} is filled with the risk types that source raised). Each reads
# as its own flowing sentence so the Additional-Inputs block is an eye-catching narrative, not a list.
_SOURCE_STORY: dict[str, str] = {
    "SI History":       "**SI history** received flagging risks associated with {items}",
    "Legal":            "**Legal input** received concerning risks related to {items}",
    "Additional Input": "**Additional input** received concerning risks related to {items}",
    "Management":        "**Management** highlighted risks related to {items}",
    "Past Audit":       "**Past audit observations** identified risks associated with {items}",
}


def _natural_join(items: list[str]) -> str:
    """Join phrases into readable prose — 'a', 'a and b', or 'a, b, and c' (de-duped, order preserved)."""
    items = list(dict.fromkeys(i for i in items if i))
    if len(items) <= 1:
        return items[0] if items else ""
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _collect_by_source(parts: ScopeSummaryParts) -> list[tuple[str, str, list[str]]]:
    """(source_key, display label, de-duped risk types) for each non-system source that raised something."""
    out: list[tuple[str, str, list[str]]] = []
    for source_key, label in _SOURCE_LINE_LABELS:
        items = list(dict.fromkeys(t.strip() for t in parts.by_source.get(source_key, []) if t.strip()))
        if items:
            out.append((source_key, label, items))
    return out


def _additional_inputs_story_fallback(by_source: list[tuple[str, str, list[str]]]) -> str:
    """Deterministic narrative — one framed sentence per source, risk types kept verbatim. Used when no
    LLM is available (or the LLM narrative fails), so this section never breaks and never invents text."""
    lines: list[str] = []
    for source_key, label, items in by_source:
        joined = _natural_join(items)
        template = _SOURCE_STORY.get(source_key) or (f"**{label}** also raised " + "{items}")
        lines.append("• " + template.format(items=joined) + ".")
    return "\n".join(lines)


_ADDITIONAL_SYSTEM_PROMPT = (
    "You write the 'Additional Inputs' section of an audit scope summary. You are given, per SOURCE, the "
    "risk types that source separately raised for ONE scope area. Write ONE short, natural bullet "
    "sentence per source, each starting with '• ' and leading with the source name in **bold**, stating "
    "what that source raised.\n"
    "STRICT: keep every given risk type faithfully — do NOT add, drop, merge away, or reword the risk "
    "types themselves; you may only add light connective words (e.g. 'concerning', 'highlighting', "
    "'risks related to', 'risks associated with', 'and'). Write flowing, professional prose and vary the "
    "phrasing by source so it reads naturally. Examples of the desired style:\n"
    "  • **Additional input** received concerning risks related to Travel, Meals, and Expenses.\n"
    "  • **Additional input** received highlighting risks associated with Travel, Meals, and Expenses.\n"
    "  • **Management** highlighted risks related to <…>.\n"
    "  • **Past audit observations** identified risks associated with <…>.\n"
    "  • **Legal input** received concerning risks related to <…>.\n"
    "  • **SI history** received flagging risks associated with <…>.\n"
    "One bullet per source, in the order given. Output ONLY the bullet lines — no preamble, no headers."
)


def _additional_inputs_story(llm: BaseChatModel | None, scope_area: str,
                             parts: ScopeSummaryParts) -> str:
    """Narrate what the non-system sources raised for this scope — one natural sentence per source, as a
    bullet. When an LLM is available it phrases the sentences dynamically (constrained to the given risk
    types — nothing added/invented); otherwise a deterministic fallback is used. Empty when none raised."""
    by_source = _collect_by_source(parts)
    if not by_source:
        return ""
    if llm is not None:
        try:
            import json

            from langchain_core.messages import HumanMessage, SystemMessage

            payload = {"scope_area": scope_area,
                       "sources": {label: items for _key, label, items in by_source}}
            resp = llm.invoke([
                SystemMessage(content=_ADDITIONAL_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ])
            lines = [ln.strip() for ln in (resp.content or "").splitlines() if ln.strip().startswith("•")]
            if lines:
                return "\n".join(lines)
            logger.warning("[AIScope] Additional-inputs LLM narrative empty — using deterministic")
        except Exception as exc:  # noqa: BLE001 — never break the cell on an LLM hiccup
            logger.warning("[AIScope] Additional-inputs LLM narrative failed — using deterministic | %s", exc)
    return _additional_inputs_story_fallback(by_source)


def build_summary_cell(llm: BaseChatModel | None, scope_area: str, parts: ScopeSummaryParts) -> str:
    """Build the full Sheet B 'Summary of Risks' cell: the system STORY (already generated as a
    narrative by the summary generator — used as-is, never re-structured) plus a short story of what
    the other sources raised."""
    system_story = "\n".join(t.strip() for t in parts.system if t.strip())
    additional = _additional_inputs_story(llm, scope_area, parts)
    if system_story and additional:
        return f"{system_story}\n\n{additional}"
    return system_story or additional
