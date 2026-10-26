from __future__ import annotations

from pathlib import Path

import pandas as pd


def list_output_files(directory: Path) -> list[Path]:
    """Return xlsx audit output files sorted newest-first."""
    if not directory.exists():
        return []
    return sorted(directory.glob("TPI_Due_Diligence_Audit_*.xlsx"), reverse=True)


def load_excel(path: Path) -> pd.DataFrame:
    """Load 'Output A - Risk Detail' sheet and normalise to strings."""
    try:
        df = pd.read_excel(path, sheet_name="Output A - Risk Detail", dtype=str)
    except Exception as exc:
        raise ValueError(f"Cannot read '{path.name}': {exc}") from exc

    df = df.fillna("").apply(lambda col: col.str.strip() if col.dtype == object else col)
    return df


def get_summary_stats(df: pd.DataFrame) -> dict[str, int]:
    """Count rows per risk classification."""
    title_col = "Risk Title" if "Risk Title" in df.columns else df.columns[1]
    counts: dict[str, int] = {
        "Potential Risk":          0,
        "Further Review Required": 0,
        "No Risk Identified":      0,
        "Total":                   len(df),
    }
    for val in df[title_col]:
        for key in counts:
            if key != "Total" and key.lower() in str(val).lower():
                counts[key] += 1
                break
    return counts
