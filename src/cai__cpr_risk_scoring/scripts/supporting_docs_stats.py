"""
supporting_docs_stats.py — Per-auditor file statistics from the supporting docs volume.

Shows four metrics for each auditor folder under AUDITORS_SUPPORTING_DOCS_VOLUME_PATH:
  1. Max file size
  2. Average file size
  3. Total documents
  4. Average docs across all auditors (printed once at the end)

Usage:
    python scripts/supporting_docs_stats.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

from src.config.constants import SUPPORTING_DOCS_VOLUME_PATH
from src.config.settings import get_settings

BYTES_PER_MB = 1_048_576


def _fmt_size(size_bytes: int) -> str:
    if size_bytes >= BYTES_PER_MB:
        return f"{size_bytes / BYTES_PER_MB:.2f} MB"
    return f"{size_bytes / 1024:.1f} KB"


def list_top_level_dirs(host: str, headers: dict, volume_path: str) -> list[str]:
    """Return the direct child directory paths under volume_path."""
    url = f"{host}/api/2.0/fs/directories{volume_path}"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    contents = resp.json().get("contents", [])
    return [item["path"] for item in contents if item.get("is_directory")]


def list_files_recursive(host: str, headers: dict, volume_path: str) -> list[dict]:
    """Recursively list all files under volume_path. Returns list of {path, name, size}."""
    url = f"{host}/api/2.0/fs/directories{volume_path}"
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        contents = resp.json().get("contents", [])
    except Exception as exc:
        print(f"  [warn] Cannot list {volume_path}: {exc}", file=sys.stderr)
        return []

    files: list[dict] = []
    for item in contents:
        item_path = item.get("path", "")
        if item.get("is_directory"):
            files.extend(list_files_recursive(host, headers, item_path))
        else:
            files.append({
                "path": item_path,
                "name": item.get("name", Path(item_path).name),
                "size": item.get("file_size", 0) or 0,
            })
    return files


def main() -> None:
    settings = get_settings()
    host = settings.databricks_host.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.databricks_token}"}

    print(f"\nVolume : {SUPPORTING_DOCS_VOLUME_PATH}")
    print("Fetching auditor folders...\n")

    auditor_dirs = list_top_level_dirs(host, headers, SUPPORTING_DOCS_VOLUME_PATH)
    if not auditor_dirs:
        print("No auditor folders found.")
        return

    results: list[dict] = []

    for auditor_path in sorted(auditor_dirs):
        auditor_name = auditor_path.rstrip("/").split("/")[-1]
        print(f"  Scanning {auditor_name}...", end=" ", flush=True)

        files = list_files_recursive(host, headers, auditor_path)
        total = len(files)

        if total == 0:
            print("0 files")
            results.append({"name": auditor_name, "total": 0, "max_size": 0, "avg_size": 0.0})
            continue

        sizes = [f["size"] for f in files]
        max_size = max(sizes)
        avg_size = sum(sizes) / total
        print(f"{total} files")

        results.append({
            "name": auditor_name,
            "total": total,
            "max_size": max_size,
            "avg_size": avg_size,
        })

    if not results:
        print("No data collected.")
        return

    avg_docs_across_auditors = sum(r["total"] for r in results) / len(results)

    col_w = max(len(r["name"]) for r in results) + 2
    print()
    print("=" * (col_w + 52))
    print(f"  {'Auditor':<{col_w}}  {'Total Docs':>10}  {'Max File Size':>15}  {'Avg File Size':>15}")
    print(f"  {'-'*col_w}  {'-'*10}  {'-'*15}  {'-'*15}")
    for r in results:
        print(
            f"  {r['name']:<{col_w}}  {r['total']:>10}  "
            f"{_fmt_size(r['max_size']):>15}  {_fmt_size(int(r['avg_size'])):>15}"
        )
    print("=" * (col_w + 52))
    print(f"\n  Average docs across all auditors : {avg_docs_across_auditors:.1f}\n")


if __name__ == "__main__":
    main()
