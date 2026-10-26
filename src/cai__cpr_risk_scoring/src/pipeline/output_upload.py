"""Upload a finished output file to the risk_analysis_output UC Volume.

One timestamped copy per run is uploaded (named ``<engagement>_<label>_<timestamp><ext>``) so every
rerun is kept and traceable — nothing is overwritten. Authenticated via the service principal
(src.config.databricks_auth). Best-effort: a failed upload logs a warning and never breaks the run
(the local copy is always kept).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.config.databricks_auth import build_config
from src.utils.logger import get_logger

logger = get_logger(__name__)


def upload_output_file(
    settings,
    local_path,
    *,
    label: str = "output",
    dest_volume: str | None = None,
    keep_name: bool = False,
) -> str | None:
    """Upload ``local_path`` to a UC Volume. Never raises; returns the destination path or None.

    - ``dest_volume``: target Volume base (defaults to settings.risk_analysis_output_volume_path).
    - ``keep_name=False`` (default): a NEW timestamped copy per run, named
      ``<engagement>_<label>_<timestamp><ext>`` (nothing overwritten — full history kept).
    - ``keep_name=True``: upload under the file's OWN name, overwriting the previous one (latest only,
      e.g. the Scoping Deck for download).
    """
    local_path = Path(local_path)
    base = (dest_volume or getattr(settings, "risk_analysis_output_volume_path", "") or "").rstrip("/")
    if not base:
        logger.warning("[OutputUpload] No output volume configured — skipping upload")
        return None
    if not local_path.exists():
        logger.warning("[OutputUpload] Local file missing — skipping upload | %s", local_path)
        return None

    if keep_name:
        name = local_path.name
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{settings.engagement_slug}_{label}_{stamp}{local_path.suffix}"
    dest = f"{base}/{name}"
    try:
        from databricks.sdk import WorkspaceClient

        client = WorkspaceClient(config=build_config(settings))
        with open(local_path, "rb") as fh:
            client.files.upload(dest, fh, overwrite=True)
        logger.info("[OutputUpload] Uploaded %s -> %s", local_path.name, dest)
        return dest
    except Exception as exc:  # noqa: BLE001 — best-effort; keep the local copy
        logger.warning("[OutputUpload] Upload failed (local copy kept) | dest=%s | %s", dest, exc)
        return None
