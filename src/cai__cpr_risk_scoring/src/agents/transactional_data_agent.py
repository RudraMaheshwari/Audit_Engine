import re

from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_community.agent_toolkits.sql.base import create_sql_agent
from langchain_community.utilities import SQLDatabase
from langchain_core.language_models import BaseChatModel

from src.config.constants import (
    AGENT_TRANSACTIONAL,
    DASHBOARD_TABLE_DESCRIPTIONS,
    DASHBOARD_WORKFLOW_NAMES,
    SQL_SAMPLE_ROWS_IN_TABLE_INFO,
    TRANSACTIONAL_SQL_MAX_ITERATIONS,
)
from src.config.databricks_auth import build_engine
from src.config.settings import Settings
from src.prompts.transactional_data_dictionary import TRANSACTIONAL_DATA_DICTIONARY
from src.prompts.transactional_data_prompt import SQL_PREFIX_TEMPLATE
from src.utils.logger import get_logger

logger = get_logger(__name__)


class TransactionalDataAgent:
    """
    NL-to-SQL agent for querying the 7 transactional dashboard tables (schema: cpr__gld).
    Conditionally invoked by the supervisor — only when RiskEvaluationAgent
    detects transactional or quantitative intent in the question/response.

    Uses LangChain's create_sql_agent + SQLDatabaseToolkit + sqlalchemy-databricks dialect.
    """

    def __init__(self, llm: BaseChatModel, settings: Settings) -> None:
        self._compiled = self._compile(llm, settings)
        logger.info(
            "[TransactionalDataAgent] Compiled | name=%s | tables=%s",
            AGENT_TRANSACTIONAL,
            list(settings.dashboard_table_names.values()),
        )

    @staticmethod
    def _build_tables_block(settings: Settings) -> str:
        return "\n".join(
            f"  - {name}    ({DASHBOARD_TABLE_DESCRIPTIONS.get(key, 'transactional data')})"
            for key, name in settings.dashboard_table_names.items()
        )

    def _compile(self, llm: BaseChatModel, settings: Settings):
        db = self._build_databricks_db(settings)
        toolkit = SQLDatabaseToolkit(db=db, llm=llm)

        prefix = SQL_PREFIX_TEMPLATE.format(
            scope_id=settings.current_scope_id,
            tables_block=self._build_tables_block(settings),
            engagement_filter=settings.engagement_filter_clause,
        ) + "\n\n" + TRANSACTIONAL_DATA_DICTIONARY
        return create_sql_agent(
            llm=llm,
            toolkit=toolkit,
            agent_type="openai-tools",
            prefix=prefix,
            verbose=True,
            handle_parsing_errors=True,
            max_iterations=TRANSACTIONAL_SQL_MAX_ITERATIONS,
            name=AGENT_TRANSACTIONAL,
            return_intermediate_steps=True,  # expose executed SQL + results for deterministic calc evidence
        )

    # ── Deterministic calc-evidence extraction from the agent's intermediate steps ───────
    # The SQL toolkit's query tool is named "sql_db_query"; its tool_input carries the exact SQL the
    # agent ran, and the observation is the result. Reading these is deterministic — it does NOT depend
    # on the LLM echoing the query or table names correctly in its prose answer.
    _SQL_TABLE_RE = re.compile(
        r"\bfrom\s+([A-Za-z0-9_.`]+)|\bjoin\s+([A-Za-z0-9_.`]+)", re.IGNORECASE
    )

    @classmethod
    def _tables_in_sql(cls, sql: str) -> list[str]:
        """Dedicated workflow name(s) for the table(s) in the FROM/JOIN clauses — the SAME names the
        transactional-analysis run uses (DASHBOARD_WORKFLOW_NAMES), so calc evidence is consistent.
        Strips catalog/schema to the base table, maps to the dedicated name, order-preserving + unique."""
        seen: list[str] = []
        for m in cls._SQL_TABLE_RE.finditer(sql or ""):
            raw = (m.group(1) or m.group(2) or "").strip("`").rstrip(";")
            if not raw or raw.lower() in ("select", "("):
                continue
            base = raw.split(".")[-1].strip("`")
            name = DASHBOARD_WORKFLOW_NAMES.get(base, base)
            if name not in seen:
                seen.append(name)
        return seen

    @classmethod
    def extract_executed_sql(cls, result: dict) -> list[tuple[str, str]]:
        """[(sql, result_text), …] for each query the agent actually executed (from intermediate_steps)."""
        pairs: list[tuple[str, str]] = []
        for step in (result or {}).get("intermediate_steps", []) or []:
            try:
                action, observation = step
            except Exception:  # noqa: BLE001
                continue
            if getattr(action, "tool", "") != "sql_db_query":
                continue
            ti = getattr(action, "tool_input", "")
            sql = (ti.get("query") if isinstance(ti, dict) else str(ti)).strip()
            if sql:
                pairs.append((sql, str(observation)))
        return pairs

    @classmethod
    def build_exact_tables_block(cls, result: dict) -> str:
        """Deterministic 'Tables & queries executed' block (exact FROM-clause table names + the SQL)."""
        pairs = cls.extract_executed_sql(result)
        if not pairs:
            return ""
        lines = ["Tables & queries executed (exact, from the SQL actually run):"]
        for i, (sql, _res) in enumerate(pairs, 1):
            tbls = ", ".join(cls._tables_in_sql(sql)) or "(none parsed)"
            lines.append(f"[{i}] Tables: {tbls}")
            lines.append(f"    SQL: {' '.join(sql.split())}")
        return "\n".join(lines)

    @staticmethod
    def _patch_databricks_dialect() -> None:
        # Databricks SQLAlchemy dialect crashes on VOID-typed columns (null/dropped Delta
        # columns) because 'void' is absent from its internal type map. This patches
        # get_columns to fall back to DESCRIBE TABLE on KeyError, skipping void columns.
        try:
            from databricks.sqlalchemy.dialect import DatabricksDialect
            import sqlalchemy.types as _sa_types
            from sqlalchemy import text as _text

            _orig = DatabricksDialect.get_columns

            def _safe_get_columns(self, connection, table_name, schema=None, **kw):
                try:
                    return _orig(self, connection, table_name, schema, **kw)
                except KeyError:
                    full = f"`{schema}`.`{table_name}`" if schema else f"`{table_name}`"
                    rows = connection.execute(_text(f"DESCRIBE TABLE {full}")).fetchall()
                    _type_map = {
                        "string": _sa_types.String, "varchar": _sa_types.String,
                        "char": _sa_types.String,   "text": _sa_types.String,
                        "int": _sa_types.Integer,   "integer": _sa_types.Integer,
                        "bigint": _sa_types.BigInteger, "long": _sa_types.BigInteger,
                        "smallint": _sa_types.SmallInteger, "tinyint": _sa_types.SmallInteger,
                        "double": _sa_types.Float,  "float": _sa_types.Float,
                        "decimal": _sa_types.Numeric, "numeric": _sa_types.Numeric,
                        "boolean": _sa_types.Boolean, "bool": _sa_types.Boolean,
                        "date": _sa_types.Date,     "timestamp": _sa_types.DateTime,
                        "timestamp_ntz": _sa_types.DateTime, "binary": _sa_types.LargeBinary,
                    }
                    cols = []
                    for row in rows:
                        col_name = (row[0] or "").strip()
                        col_type_str = (row[1] or "").lower().strip()
                        if not col_name or col_name.startswith("#"):
                            break
                        if col_type_str == "void":
                            continue
                        base = col_type_str.split("(")[0].split("<")[0]
                        cols.append({
                            "name": col_name,
                            "type": _type_map.get(base, _sa_types.String)(),
                            "nullable": True,
                            "default": None,
                            "autoincrement": False,
                            "comment": row[2] if len(row) > 2 else None,
                        })
                    return cols

            DatabricksDialect.get_columns = _safe_get_columns
        except Exception:
            pass

    @staticmethod
    def _build_databricks_db(settings: Settings) -> SQLDatabase:
        TransactionalDataAgent._patch_databricks_dialect()
        engine = build_engine(settings, schema=settings.databricks_transactional_schema)
        include_tables = list(settings.dashboard_table_names.values())
        db = SQLDatabase(
            engine,
            schema=settings.databricks_transactional_schema,
            include_tables=include_tables,
            sample_rows_in_table_info=SQL_SAMPLE_ROWS_IN_TABLE_INFO,
        )
        logger.info(
            "[TransactionalDataAgent] DB connected | catalog=%s | schema=%s | tables=%s",
            settings.databricks_catalog, settings.databricks_transactional_schema, include_tables,
        )
        return db

    def get_agent(self):
        """Return the compiled SQL agent for use by the supervisor."""
        return self._compiled
