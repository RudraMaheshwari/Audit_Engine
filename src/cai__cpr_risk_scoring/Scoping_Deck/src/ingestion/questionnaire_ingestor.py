"""Ingests questionnaire Q&A (IDR + Planning) into the questionnaire vector index."""

from __future__ import annotations

from src.models.schemas import AuditQuestion
from src.repositories.questionnaire_repo import DatabricksQuestionnaireRepository
from src.utils.logger import get_logger

from ..config import ScopingDeckConfig
from .questionnaire_index import QuestionnaireChunk, QuestionnaireIndexManager

logger = get_logger(__name__)


class QuestionnaireIngestor:
    """Loads the configured questionnaire types for the auditor and upserts them to the index.

    When volume_path is supplied (Sam_1 case), data is loaded from that Databricks Volumes path
    first.  Falls back to the table-based DatabricksQuestionnaireRepository when the volume path
    is absent or returns no rows.
    """

    def __init__(
        self,
        config: ScopingDeckConfig,
        index: QuestionnaireIndexManager,
        volume_path: str | None = None,
    ) -> None:
        self._config = config
        self._index = index
        self._volume_path = volume_path
        self._repo = DatabricksQuestionnaireRepository(config.settings)

    def ingest(self, force: bool = False) -> int:
        """Ingest all configured questionnaire types; return the number of chunks upserted."""
        auditor_id = self._config.settings.auditor_id
        scope_id = self._config.settings.current_scope_id

        self._index.ensure_index()

        if not force and self._index.is_populated(auditor_id=auditor_id):
            logger.info(
                "[Ingest] questionnaire_index already populated for auditor=%s — skipping "
                "(pass force=True to re-ingest)", auditor_id,
            )
            return 0

        chunks = self._load_chunks_from_volume(auditor_id) or self._load_chunks(scope_id, auditor_id)
        if not chunks:
            logger.warning("[Ingest] No questionnaire chunks to upsert — nothing ingested")
            return 0

        self._index.upsert(chunks)
        logger.info("[Ingest] Done | total_chunks=%d | auditor=%s", len(chunks), auditor_id)
        return len(chunks)

    # Case-insensitive column aliases for known Q&A fields. This loader matches on the column name
    # lower-cased WITHOUT replacing spaces, so multi-word aliases are the SPACE form (e.g. "management
    # response"). Two source schemas: IDR (question="Data and Report Request", response="Management
    # Response", follow-ups="FollowUp Question/Response") and Planning (question="Question",
    # response="Company Response").
    _COL_ALIASES: dict[str, list[str]] = {
        "question_id":        ["question_id", "q_id", "qid", "id", "question no"],
        "question_text":      ["question_text", "question", "q_text", "q", "data and report request"],
        "response_text":      ["response_text", "response", "company response", "management response", "answer", "resp", "a"],
        "followup_question":  ["followup_question", "followup question", "follow_up_question", "follow up question", "followup_q", "fup_question"],
        "followup_response":  ["followup_response", "followup response", "follow_up_response", "follow up response", "followup_a", "fup_response"],
        "scope_id":           ["scope_id", "scope"],
        "questionnaire_type": ["questionnaire_type", "type", "qtype", "q_type"],
        "source_table":       ["source_table", "source", "file_name", "filename"],
    }

    def _load_chunks_from_volume(self, auditor_id: str) -> list[QuestionnaireChunk]:
        """Load Q&A chunks from a Databricks Volumes path via the Databricks SDK.

        Uses the workspace host + token already in Settings — no separate credentials needed.
        All columns in each file are read; known Q&A fields are mapped by case-insensitive alias;
        every remaining column is preserved in metadata (original_columns) so nothing is lost.
        Falls back to the table-based repo when the path is not configured, the directory is empty,
        or the SDK call fails.
        """
        if not self._volume_path:
            return []

        try:
            from databricks.sdk import WorkspaceClient
            import io
            import pandas as pd

            from src.config.databricks_auth import build_config

            client = WorkspaceClient(config=build_config(self._config.settings))

            # Normalise path: strip trailing slash for the SDK call.
            vol_path = self._volume_path.rstrip("/")

            # List all files in the directory.
            try:
                entries = list(client.files.list_directory_contents(directory_path=vol_path))
            except Exception as exc:
                logger.info(
                    "[Ingest] Volume directory not accessible — using table repo | path=%s | %s",
                    vol_path, exc,
                )
                return []

            supported = {".parquet", ".json", ".csv", ".xlsx"}
            file_entries = [e for e in entries if not e.is_directory
                            and any((e.path or "").endswith(ext) for ext in supported)]

            if not file_entries:
                logger.info(
                    "[Ingest] Volume directory has no supported files — using table repo | path=%s",
                    vol_path,
                )
                return []

            logger.info("[Ingest] Found %d file(s) in volume | path=%s", len(file_entries), vol_path)

            frames: list[pd.DataFrame] = []
            for entry in file_entries:
                file_path = entry.path or ""
                try:
                    raw = client.files.download(file_path=file_path)
                    data = raw.contents.read()
                    if file_path.endswith(".parquet"):
                        df = pd.read_parquet(io.BytesIO(data))
                    elif file_path.endswith(".json"):
                        df = pd.read_json(io.BytesIO(data))
                    elif file_path.endswith(".xlsx"):
                        df = pd.read_excel(io.BytesIO(data))
                    else:
                        df = pd.read_csv(io.BytesIO(data))
                    logger.info(
                        "[Ingest] Read volume file | file=%s | rows=%d | cols=%s",
                        file_path.split("/")[-1], len(df), list(df.columns),
                    )
                    frames.append(df)
                except Exception as exc:
                    logger.warning("[Ingest] Skipping unreadable volume file %s | %s", file_path, exc)

            if not frames:
                return []

            df_all = pd.concat(frames, ignore_index=True)
            # Build a lowercase-column → actual-column map once for the combined frame.
            col_map = {c.lower(): c for c in df_all.columns}

            def _get(row: "pd.Series", field: str, default: str = "") -> str:
                for alias in self._COL_ALIASES.get(field, [field]):
                    actual = col_map.get(alias.lower())
                    if actual and actual in row.index:
                        val = row[actual]
                        if val is not None and str(val).strip() not in ("", "nan", "None"):
                            return str(val).strip()
                return default

            # Columns NOT mapped to a known Q&A field → go into original_columns / metadata.
            known_actual = {
                col_map[alias.lower()]
                for aliases in self._COL_ALIASES.values()
                for alias in aliases
                if alias.lower() in col_map
            }
            extra_cols = [c for c in df_all.columns if c not in known_actual]

            chunks: list[QuestionnaireChunk] = []
            for idx, row in df_all.iterrows():
                q_id = _get(row, "question_id") or str(idx)
                q_text = _get(row, "question_text")
                r_text = _get(row, "response_text")
                if not q_text and not r_text:
                    continue   # skip rows with no usable content

                extra = {c: str(row[c]) for c in extra_cols
                         if row[c] is not None and str(row[c]).strip() not in ("", "nan", "None")}

                q = AuditQuestion(
                    question_id=q_id,
                    question_text=q_text,
                    response_text=r_text,
                    followup_question=_get(row, "followup_question"),
                    followup_response=_get(row, "followup_response"),
                    scope_id=_get(row, "scope_id", self._config.settings.current_scope_id),
                    auditor_id=auditor_id,
                    questionnaire_type=_get(row, "questionnaire_type", "idr"),
                    source_table=_get(row, "source_table", vol_path),
                    original_columns=extra,   # all unmapped columns preserved here
                )
                chunk = self._to_chunk(q)
                if chunk.content:
                    chunks.append(chunk)

            logger.info(
                "[Ingest] Volume load complete | chunks=%d | extra_cols=%s | path=%s",
                len(chunks), extra_cols, vol_path,
            )
            return chunks

        except Exception as exc:
            logger.warning("[Ingest] Volume load failed — falling back to table repo | %s", exc)
            return []

    def _load_chunks(self, scope_id: str, auditor_id: str) -> list[QuestionnaireChunk]:
        chunks: list[QuestionnaireChunk] = []
        for qtype in self._config.questionnaire_types:
            try:
                questions = self._repo.load_all(
                    scope_id=scope_id, auditor_id=auditor_id, questionnaire_type=qtype
                )
            except Exception as exc:
                logger.error("[Ingest] Failed to load questionnaire_type=%s | %s", qtype, exc)
                continue

            typed = [self._to_chunk(q) for q in questions if self._compose_content(q)]
            logger.info(
                "[Ingest] Loaded %d Q&A (%d ingestable) | type=%s | auditor=%s",
                len(questions), len(typed), qtype, auditor_id,
            )
            chunks.extend(typed)
        return chunks

    @classmethod
    def _to_chunk(cls, question: AuditQuestion) -> QuestionnaireChunk:
        return QuestionnaireChunk(
            chunk_id=f"qn::{question.auditor_id}::{question.questionnaire_type}::{question.question_id}",
            content=cls._compose_content(question),
            question_id=question.question_id,
            question_text=question.question_text,
            response_text=question.response_text,
            followup_question=question.followup_question or "",
            followup_response=question.followup_response or "",
            scope_id=question.scope_id,
            auditor_id=question.auditor_id,
            questionnaire_type=question.questionnaire_type,
            source_table=question.source_table,
            metadata={
                "question_id": question.question_id,
                "questionnaire_type": question.questionnaire_type,
                "source_table": question.source_table,
                "original_columns": question.original_columns,
            },
        )

    @staticmethod
    def _compose_content(question: AuditQuestion) -> str:
        parts = [f"Question: {question.question_text}", f"Response: {question.response_text}"]
        if question.followup_question:
            parts.append(f"Follow-up Question: {question.followup_question}")
        if question.followup_response:
            parts.append(f"Follow-up Response: {question.followup_response}")
        return "\n".join(p for p in parts if p and p.strip()).strip()
