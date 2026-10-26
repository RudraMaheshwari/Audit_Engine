from __future__ import annotations

import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import requests

from src.config.constants import SUPPORTED_VOLUME_EXTENSIONS
from src.utils.logger import get_logger

logger = get_logger(__name__)


def normalize_volume_path(path: str) -> str:
    """Return a Files-API filesystem path. Accepts either the proper '/Volumes/<cat>/<schema>/<vol>/…'
    form OR dot notation '<cat>.<schema>.<vol>[/subpath]' (which the Files API cannot use) and converts
    the latter. A path that already starts with '/' is returned unchanged."""
    p = (path or "").strip()
    if not p or p.startswith("/"):
        return p
    head, _, tail = p.partition("/")
    parts = [seg for seg in head.split(".") if seg]
    if len(parts) >= 3:  # catalog.schema.volume[.more] -> /Volumes/catalog/schema/volume...
        base = "/Volumes/" + "/".join(parts)
        return f"{base}/{tail}" if tail else base
    return "/" + p  # best effort: at least give the URL a leading slash


def download_volume_file(host: str, token: str, volume_path: str, local_path: str | Path) -> bool:
    local_path = Path(local_path)
    volume_path = normalize_volume_path(volume_path)
    url = f"{host.rstrip('/')}/api/2.0/fs/files{quote(volume_path, safe='/')}"
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=120)
        resp.raise_for_status()
        local_path.write_bytes(resp.content)
        logger.info("[VolumeDownloader] Downloaded file  name=%s  bytes=%d", local_path.name, len(resp.content))
        return True
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "")
        logger.info("[VolumeDownloader] Raw download failed (HTTP %s) — retrying via SDK | name=%s",
                    status, local_path.name)
    # SDK fallback — recovers any Unicode-named file that 403s the raw Files API (nothing hardcoded).
    if DatabricksVolumeDownloader(host, token)._sdk_download(volume_path, local_path):
        logger.info("[VolumeDownloader] Recovered via SDK  name=%s", local_path.name)
        return True
    logger.warning("[VolumeDownloader] Single-file download FAILED (raw + SDK)  path=%s", volume_path)
    return False


class DatabricksVolumeDownloader:
    def __init__(self, host: str, token: str) -> None:
        self._host = host.rstrip("/")
        self._token = token
        self._headers = {"Authorization": f"Bearer {token}"}
        self._sdk = None           # lazy WorkspaceClient for the robust download fallback
        self._sdk_failed = False
        self._lock = threading.Lock()
        self._last_refresh = 0.0

    def _refresh_headers(self) -> bool:
        """Re-mint the SP OAuth bearer token (tokens expire ~hourly; a long ingest otherwise starts
        getting 403s on every file). Coalesced under a lock + a short cooldown so concurrent workers
        don't stampede the token endpoint. Returns True if a fresh token was obtained."""
        with self._lock:
            if time.time() - self._last_refresh < 10:   # another thread just refreshed
                return True
            try:
                from src.config.databricks_auth import bearer_token
                from src.config.settings import get_settings

                self._token = bearer_token(get_settings())
                self._headers = {"Authorization": f"Bearer {self._token}"}
                self._last_refresh = time.time()
                logger.info("[VolumeDownloader] Bearer token refreshed")
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("[VolumeDownloader] Token refresh failed | %s", exc)
                return False

    def _sdk_client(self):
        """Lazily build a WorkspaceClient via the SERVICE-PRINCIPAL OAuth config (auto-refreshing),
        NOT a static token. This both avoids the 'oauth and pat' auth conflict (the env already carries
        the SP client id/secret) and survives long runs without the token expiring. Any Unicode/script
        in filenames is handled by the SDK — nothing is special-cased."""
        if self._sdk is None and not self._sdk_failed:
            try:
                from databricks.sdk import WorkspaceClient

                from src.config.databricks_auth import build_config
                from src.config.settings import get_settings

                self._sdk = WorkspaceClient(config=build_config(get_settings()))
            except Exception as exc:  # noqa: BLE001
                self._sdk_failed = True
                logger.warning("[VolumeDownloader] SDK client unavailable — no download fallback | %s", exc)
        return self._sdk

    def _sdk_download(self, path: str, local_path: Path) -> bool:
        """Download via the Databricks SDK, which handles arbitrary Unicode names in any language/script
        reliably (nothing hardcoded) and auto-refreshes OAuth tokens. True on success."""
        client = self._sdk_client()
        if client is None:
            return False
        try:
            resp = client.files.download(path)
            local_path.write_bytes(resp.contents.read())
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[VolumeDownloader] SDK download failed | path=%s | error=%s", path, exc)
            return False

    def _raw_download(self, path: str, local_path: Path) -> bool:
        try:
            url = f"{self._host}/api/2.0/fs/files{quote(path, safe='/')}"
            resp = requests.get(url, headers=self._headers, timeout=120)
            resp.raise_for_status()
            local_path.write_bytes(resp.content)
            return True
        except Exception:  # noqa: BLE001 — caller handles refresh/SDK fallback
            return False

    def _list_directory(self, volume_path: str) -> list[dict]:
        url = f"{self._host}/api/2.0/fs/directories{volume_path}"
        try:
            resp = requests.get(url, headers=self._headers, timeout=30)
            resp.raise_for_status()
            contents = resp.json().get("contents", [])
        except Exception as exc:
            logger.warning("[VolumeDownloader] Cannot list directory  path=%s  error=%s", volume_path, exc)
            return []

        files: list[dict] = []
        for item in contents:
            item_path = item.get("path", "")
            if item.get("is_directory"):
                files.extend(self._list_directory(item_path))
            elif Path(item_path).suffix.lower() in SUPPORTED_VOLUME_EXTENSIONS:
                files.append({
                    "path": item_path,
                    "name": item.get("name", Path(item_path).name),
                    "size": item.get("file_size", 0),
                })
        return files

    def list_files(self, volume_path: str) -> list[dict]:
        """Return file metadata list without downloading — used for streaming pipelines."""
        return self._list_directory(normalize_volume_path(volume_path))

    def download_single_file(self, file_path: str, local_path: Path) -> bool:
        """Download one file by its volume path. Returns True on success.

        Two methods are tried so a file is NOT lost to an encoding quirk, for ANY filename in ANY
        language/script (no character is special-cased): (1) the raw Files API (fast), then (2) the
        Databricks SDK, which encodes/signs arbitrary Unicode paths reliably where the raw request can
        return 403. The fallback runs on ANY raw-download failure, not on a per-character check."""
        local_path.parent.mkdir(parents=True, exist_ok=True)
        path = normalize_volume_path(file_path)
        # 1) Raw Files API (generic percent-encoding of whatever the path contains).
        if self._raw_download(path, local_path):
            return True
        # 2) The bearer token likely expired on a long run — re-mint it and retry raw once.
        if self._refresh_headers() and self._raw_download(path, local_path):
            logger.info("[VolumeDownloader] Recovered after token refresh | name=%s", Path(path).name)
            return True
        # 3) SDK fallback (auto-refreshing SP OAuth; recovers any Unicode-named file) — never skip.
        if self._sdk_download(path, local_path):
            logger.info("[VolumeDownloader] Recovered via SDK | name=%s", Path(path).name)
            return True
        logger.warning("[VolumeDownloader] Single file download FAILED (raw + refresh + SDK) | path=%s", path)
        return False

    def download_to_directory(self, volume_path: str, target_dir: str | Path) -> int:
        target = Path(target_dir)
        files  = self._list_directory(volume_path)

        if not files:
            logger.warning("[VolumeDownloader] No supported files found  volume=%s", volume_path)
            return 0

        logger.info("[VolumeDownloader] Downloading %d file(s)  volume=%s", len(files), volume_path)
        downloaded = 0
        base = volume_path.rstrip("/")
        current_folder: str | None = None

        for file_info in files:
            file_path = file_info["path"]

            # Compute relative path from the base volume root to preserve subfolder structure
            rel = file_path[len(base):].lstrip("/")
            local_path = target / rel
            local_path.parent.mkdir(parents=True, exist_ok=True)

            # Log when entering a new subfolder
            folder = str(local_path.parent.relative_to(target)) if local_path.parent != target else "(root)"
            if folder != current_folder:
                current_folder = folder
                logger.info("[VolumeDownloader] Processing folder  folder=%s", current_folder)

            if local_path.exists():
                stem, suffix = local_path.stem, local_path.suffix
                local_path = local_path.parent / f"{stem}_{downloaded}{suffix}"

            try:
                encoded_path = quote(file_path, safe="/")
                url  = f"{self._host}/api/2.0/fs/files{encoded_path}"
                resp = requests.get(url, headers=self._headers, timeout=120, stream=True)
                resp.raise_for_status()
                local_path.write_bytes(resp.content)
                logger.info(
                    "[VolumeDownloader] Downloaded  folder=%s  file=%s  bytes=%d",
                    current_folder, local_path.name, len(resp.content),
                )
                downloaded += 1
            except Exception as exc:
                logger.warning(
                    "[VolumeDownloader] Download failed  folder=%s  file=%s  error=%s",
                    current_folder, local_path.name, exc,
                )

        logger.info(
            "[VolumeDownloader] Complete  downloaded=%d/%d  volume=%s",
            downloaded, len(files), volume_path,
        )
        return downloaded


@contextmanager
def temp_volume_download(host: str, token: str, volume_path: str):
    downloader = DatabricksVolumeDownloader(host, token)
    tmp_dir    = tempfile.mkdtemp(prefix="audit_volume_")
    try:
        count = downloader.download_to_directory(volume_path, tmp_dir)
        logger.info(
            "[VolumeDownloader] Temp dir ready  files=%d  dir=%s", count, tmp_dir,
        )
        yield Path(tmp_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.debug("[VolumeDownloader] Cleaned up temp dir: %s", tmp_dir)
