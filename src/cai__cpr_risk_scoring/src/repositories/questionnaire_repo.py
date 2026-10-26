import json
import logging
import re
from pathlib import Path
from typing import Any

import openpyxl
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, field_validator
from sqlalchemy import text

from src.config.constants import (
    IDR_COL_FOLLOWUP_QUESTION,
    IDR_COL_FOLLOWUP_RESPONSE,
    IDR_COL_ID,
    IDR_COL_QUESTION,
    IDR_COL_RESPONSE,
    PLAN_COL_COMPANY_RESPONSE,
    PLAN_COL_FOLLOWUP_QUESTION,
    PLAN_COL_FOLLOWUP_RESPONSE,
    PLAN_COL_QUESTION,
    PLAN_COL_UNIQUE_ID,
)
from src.config.databricks_auth import build_engine
from src.config.settings import Settings
from src.models.schemas import AuditQuestion
from src.prompts.questionnaire_column_prompt import (
    QUESTIONNAIRE_COLUMN_SYSTEM,
    QUESTIONNAIRE_COLUMN_USER_TEMPLATE,
)

logger = logging.getLogger(__name__)

# ── Deterministic header matching against the KNOWN column names (the ones static loading used, plus
# the aliases already established for the real IDR/Planning headers). EXACT (normalised) match only —
# no fuzzy guessing. Anything not here is left to the LLM pass. Follow-up roles additionally allow a
# trailing round number (e.g. "follow up question 2") so multiple rounds map deterministically.
# id is listed FIRST so "question no" / "question id" resolve to id, not the question column.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "id":                 ("unique id", "question no", "question number", "question id",
                           "record id", "row id", "id"),
    "question":           ("question", "data and report request", "question text",
                           "idr question", "planning question", "audit question", "q"),
    "response":           ("company response", "management response", "response",
                           "response text", "answer", "resp"),
    "followup_questions": ("followup question", "follow up question", "followup q", "fup question"),
    "followup_responses": ("followup response", "follow up response", "followup a",
                           "fup response", "followup answer"),
}


def _norm_header(header: str) -> str:
    s = str(header or "").lower().replace("_", " ").replace("-", " ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _classify_header(header: str) -> str | None:
    """Resolve a header to a role by EXACT match against the known column names. Follow-up roles also
    accept a trailing round number. Returns the role or None (→ handled by the LLM pass)."""
    n = _norm_header(header)
    if not n:
        return None
    base = re.sub(r"\s*\d+$", "", n).strip()  # drop a trailing round number for follow-up columns
    for role, aliases in _COLUMN_ALIASES.items():
        if n in aliases:
            return role
        if role.startswith("followup") and base in aliases:  # numbered follow-up round
            return role
    return None


class DatabricksQuestionnaireRepository:
    def __init__(self, settings: Settings, llm=None) -> None:
        self._settings = settings
        self._llm = llm  # optional: LLM column-mapper (handles multiple/variable follow-up columns)
        self._engine = self._build_engine(settings)

    def load_all(
        self,
        scope_id: str,
        auditor_id: str,
        questionnaire_type: str = "planning",
        table: str | None = None,
    ) -> list[AuditQuestion]:
        # `table` (an explicit per-run table) wins; otherwise resolve the single configured name.
        table = self._qualify(table) if table else self._resolve_table(questionnaire_type)
        logger.info(
            "[QuestionnaireRepo] Loading from Databricks | table=%s | scope=%s",
            table, scope_id,
        )

        sql = f"SELECT * FROM {table}"
        with self._engine.connect() as conn:
            result = conn.execute(text(sql))
            cols = [c.lower().replace(" ", "_") for c in result.keys()]
            rows = [dict(zip(cols, row)) for row in result.fetchall()]

        if not rows:
            logger.warning("[QuestionnaireRepo] No rows returned from %s", table)
            return []

        is_idr = questionnaire_type.lower() == "idr"
        # Identify the role columns: LLM mapper first (handles non-standard / multiple follow-up
        # columns), falling back to the known constant names. May return SEVERAL follow-up columns.
        mapping = self._resolve_column_mapping(cols, rows[:5], is_idr)
        col_id    = mapping["id"]
        col_q     = mapping["question"]
        col_r     = mapping["response"]
        col_fupqs = mapping["followup_questions"]
        col_fuprs = mapping["followup_responses"]
        if col_q not in cols or col_r not in cols:
            raise ValueError(
                f"Could not identify question/response columns in '{table}'. "
                f"Found columns: {sorted(cols)}"
            )
        if not col_id or col_id not in cols:
            logger.info("[QuestionnaireRepo] No id column in %s — generating row-based ids", table)
            col_id = None

        questions: list[AuditQuestion] = []
        skipped = 0
        for i, row in enumerate(rows, start=1):
            # Generate a stable row-based id when the table has no id column (or it's blank).
            q_id   = str(row.get(col_id) or "").strip() if col_id else ""
            q_id   = q_id or f"{questionnaire_type.lower()}_{i}"
            q_text = str(row.get(col_q) or "").strip()
            r_text = str(row.get(col_r) or "").strip()

            # Skip only truly-empty rows (no question AND no response) — consistent with the volume
            # loader / deck ingestor. A question WITH a blank response is kept: a non-response to a
            # compliance question is itself meaningful (the classifier flags it as a gap).
            if not q_text and not r_text:
                skipped += 1
                continue

            original: dict[str, Any] = {
                k: ("" if v is None else v) for k, v in row.items()
            }
            # Concatenate ALL follow-up columns (labeled only when there is more than one).
            fup_q = self._concat_followups(row, col_fupqs)
            fup_r = self._concat_followups(row, col_fuprs)
            questions.append(
                AuditQuestion(
                    question_id=q_id,
                    question_text=q_text,
                    response_text=r_text,
                    followup_question=fup_q,
                    followup_response=fup_r,
                    scope_id=scope_id,
                    auditor_id=auditor_id,
                    questionnaire_type=questionnaire_type,
                    source_table=table.split(".")[-1],
                    original_columns=original,
                )
            )

        # NOTE: the questionnaire Q&A is intentionally KEPT in its original (local) language — it is not
        # translated here. The LLM reads the local-language Q&A natively; the generated findings are
        # rendered bilingually (English + local) downstream. See the bilingual answer rendering.
        logger.info(
            "[QuestionnaireRepo] Loaded %d questions | skipped=%d (blank) | table=%s",
            len(questions), skipped, table,
        )
        return questions

    def _resolve_table(self, questionnaire_type: str) -> str:
        name = (
            self._settings.idr_table
            if questionnaire_type.lower() == "idr"
            else self._settings.planning_table_name
        )
        return self._qualify(name)

    def _qualify(self, table: str) -> str:
        """Fully-qualify a bare table name (catalog.schema.table); pass through if already qualified."""
        table = table.strip()
        if table.count(".") >= 2:  # already catalog.schema.table
            return table
        return f"{self._settings.databricks_catalog}.{self._settings.databricks_schema_name}.{table}"

    @staticmethod
    def _concat_followups(row: dict, columns: list[str]) -> str:
        """Join the values of one or more follow-up columns. A single column keeps its raw value; with
        several, each is labeled by its header so the multiple follow-ups stay distinguishable."""
        parts: list[str] = []
        single = len(columns) == 1
        for col in columns:
            val = str(row.get(col) or "").strip()
            if val:
                parts.append(val if single else f"[{col}] {val}")
        return "\n".join(parts)

    def _resolve_column_mapping(self, cols: list[str], sample_rows: list[dict], is_idr: bool) -> dict:
        """Identify role columns accurately: {id, question, response, followup_questions[],
        followup_responses[]}. Strategy — (1) deterministic header classification (precise, no LLM
        variance), (2) LLM ONLY for a missing/ambiguous primary question/response, (3) constant names
        as last resort. Follow-up columns are taken in table order so multiple rounds stay ordered."""
        buckets: dict[str, list[str]] = {
            "id": [], "question": [], "response": [], "followup_questions": [], "followup_responses": []
        }
        for c in cols:
            role = _classify_header(c)
            if role:
                buckets[role].append(c)

        # Primary question/response: unambiguous when exactly one candidate was classified.
        q = buckets["question"][0] if len(buckets["question"]) == 1 else None
        r = buckets["response"][0] if len(buckets["response"]) == 1 else None
        fup_q = list(buckets["followup_questions"])
        fup_r = list(buckets["followup_responses"])
        col_id = buckets["id"][0] if buckets["id"] else None
        method = "header"

        # LLM pass only when the primary question/response is missing or ambiguous.
        if self._llm is not None and (q is None or r is None):
            try:
                m = self._map_columns_llm(cols, sample_rows)
                if m:
                    method = "header+llm"
                    q = q or m["question"]
                    r = r or m["response"]
                    fup_q = fup_q or m["followup_questions"]
                    fup_r = fup_r or m["followup_responses"]
                    col_id = col_id or m["id"]
            except Exception as exc:  # noqa: BLE001
                logger.warning("[QuestionnaireRepo] LLM column mapping failed — using defaults | %s", exc)

        # Constant names as the final fallback.
        if q is None or r is None:
            method = "defaults"
            if is_idr:
                dq, dr, did = IDR_COL_QUESTION, IDR_COL_RESPONSE, IDR_COL_ID
                dfq, dfr = IDR_COL_FOLLOWUP_QUESTION, IDR_COL_FOLLOWUP_RESPONSE
            else:
                dq, dr, did = PLAN_COL_QUESTION, PLAN_COL_COMPANY_RESPONSE, PLAN_COL_UNIQUE_ID
                dfq, dfr = PLAN_COL_FOLLOWUP_QUESTION, PLAN_COL_FOLLOWUP_RESPONSE
            q = q or dq
            r = r or dr
            col_id = col_id or (did if did in cols else None)
            fup_q = fup_q or ([dfq] if dfq in cols else [])
            fup_r = fup_r or ([dfr] if dfr in cols else [])

        # Validation: a column can fill only one role (never reuse question as response or a follow-up).
        used = {q, r}
        fup_q = [c for c in fup_q if c in cols and c not in used]
        fup_r = [c for c in fup_r if c in cols and c not in used and c not in fup_q]
        if col_id in used:
            col_id = None

        logger.info(
            "[QuestionnaireRepo] columns mapped via %s | q=%s r=%s fup_q=%s fup_r=%s id=%s",
            method, q, r, fup_q, fup_r, col_id,
        )
        return {"id": col_id, "question": q, "response": r,
                "followup_questions": fup_q, "followup_responses": fup_r}

    def _map_columns_llm(self, cols: list[str], sample_rows: list[dict]) -> dict | None:
        """Ask the LLM to map columns to roles. Returns the mapping, or None if it can't name a valid
        question + response column. Only columns that actually exist are kept."""
        sample_block = "\n".join(
            " | ".join(f"{c}={str(r.get(c) or '')[:60]}" for c in cols) for r in sample_rows
        ) or "(no sample rows)"
        user = QUESTIONNAIRE_COLUMN_USER_TEMPLATE.format(headers=cols, sample_block=sample_block)
        resp = self._llm.invoke([
            SystemMessage(content=QUESTIONNAIRE_COLUMN_SYSTEM),
            HumanMessage(content=user),
        ])
        raw = (resp.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = json.loads(raw)

        def _ok(c) -> bool:
            return isinstance(c, str) and c in cols

        q, r = data.get("question"), data.get("response")
        if not (_ok(q) and _ok(r)):
            return None
        return {
            "id": data.get("id") if _ok(data.get("id")) else None,
            "question": q,
            "response": r,
            "followup_questions": [c for c in (data.get("followup_questions") or []) if _ok(c)],
            "followup_responses": [c for c in (data.get("followup_responses") or []) if _ok(c)],
        }

    @staticmethod
    def _build_engine(settings: Settings):
        return build_engine(settings, schema=settings.databricks_schema_name)


class QuestionnaireRepoConfig(BaseModel):
    file_path: str
    scope_id: str
    auditor_id: str
    questionnaire_type: str = "planning"
    question_id_col: str = "unique_id"
    question_text_col: str = "question"
    response_text_col: str = "company_response"
    sheet_name: str | int = 0

    @field_validator("questionnaire_type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v.lower() not in {"planning", "idr"}:
            raise ValueError("questionnaire_type must be 'planning' or 'idr'")
        return v.lower()


class QuestionnaireRepository:
    def __init__(self, config: QuestionnaireRepoConfig) -> None:
        self._config = config

    def load_all(self) -> list[AuditQuestion]:
        path = Path(self._config.file_path)
        if not path.exists():
            raise FileNotFoundError(f"Questionnaire file not found: {path}")

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = (
            wb.worksheets[self._config.sheet_name]
            if isinstance(self._config.sheet_name, int)
            else wb[self._config.sheet_name]
        )

        headers = [
            str(cell.value).strip().lower() if cell.value else ""
            for cell in next(ws.iter_rows(max_row=1))
        ]
        self._validate_headers(headers)

        col_idx    = {h: i for i, h in enumerate(headers)}
        q_id_col   = col_idx[self._config.question_id_col.lower()]
        q_text_col = col_idx[self._config.question_text_col.lower()]
        r_text_col = col_idx[self._config.response_text_col.lower()]

        questions: list[AuditQuestion] = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not any(row):
                continue
            original = {
                headers[i]: (row[i] if row[i] is not None else "")
                for i in range(len(headers))
            }
            questions.append(
                AuditQuestion(
                    question_id=str(row[q_id_col] or "").strip(),
                    question_text=str(row[q_text_col] or "").strip(),
                    response_text=str(row[r_text_col] or "").strip(),
                    scope_id=self._config.scope_id,
                    auditor_id=self._config.auditor_id,
                    questionnaire_type=self._config.questionnaire_type,
                    original_columns=original,
                )
            )

        wb.close()
        logger.info("Loaded %d questions from %s", len(questions), path.name)
        return questions

    def _validate_headers(self, headers: list[str]) -> None:
        required = {
            self._config.question_id_col.lower(),
            self._config.question_text_col.lower(),
            self._config.response_text_col.lower(),
        }
        missing = required - set(headers)
        if missing:
            raise ValueError(
                f"Questionnaire file missing required columns: {missing}"
            )
