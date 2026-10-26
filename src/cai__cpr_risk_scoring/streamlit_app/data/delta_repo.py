"""Read Delta output tables from the Databricks SQL warehouse → pandas DataFrames.

Used by the semantic-comparison tabs to load an output table and its `_copy` (chat-edited) twin.
All names are fully qualified (`catalog.schema.table`), so no session schema is needed.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from config import catalog, get_sql_engine, output_schema


def fqn(table_base: str, engagement: str, copy: bool = False) -> str:
    """Build `catalog.schema.<base>_<engagement>[_copy]` for an output table."""
    name = f"{table_base}_{engagement}" + ("_copy" if copy else "")
    return f"{catalog()}.{output_schema()}.{name}"


@st.cache_data(ttl=120, show_spinner=False)
def read_table(table_fqn: str, limit: int | None = 5000) -> pd.DataFrame:
    """Read a (fully-qualified) Delta table into a DataFrame. Raises on a bad name so the UI can show it."""
    sql = f"SELECT * FROM {table_fqn}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return pd.read_sql(sql, get_sql_engine())
