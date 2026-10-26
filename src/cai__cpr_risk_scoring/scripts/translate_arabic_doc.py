"""Standalone test: download a document from a Databricks Volume, detect its
language, and — if it is a non-English file (e.g. Arabic `ar` or Urdu `ur`) —
translate it to English.

Two translation paths:
  1. NATIVE text-layer, English            -> no translation (skip).
  2. Non-English (ar/ur/any) OR scanned PDF -> ASYNC Document Translation via
     Azure Blob Storage. This path uploads the file to a blob container, runs
     Azure AI Translator's *batch Document Translation* API (which performs OCR
     on scanned PDFs internally and preserves the document format), then
     downloads the translated English document back. The translated file is
     then ready to be fed back into Azure Document Intelligence / the ingestion
     pipeline.

Why blob + async?
    Azure's *synchronous* Document Translation historically rejects PDF
    ("The format parameter is not valid"). The *asynchronous batch* API supports
    PDF and OCRs scanned pages, but requires Blob Storage (source + target).

Run:
    python scripts/translate_arabic_doc.py

Required packages:
    pip install --upgrade python-dotenv requests pymupdf azure-identity azure-storage-blob

REQUIRED ENVIRONMENT VARIABLES (.env) — see the block printed by --help-env below.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pymupdf as fitz
import requests
from dotenv import load_dotenv

# Corporate networks often TLS-intercept some hosts (e.g. api.cognitive.microsofttranslator.com)
# with a corporate root CA that Python's bundled certifi does not trust, causing
# "CERTIFICATE_VERIFY_FAILED". truststore makes Python use the Windows certificate store
# (which already trusts the corporate CA). Best-effort: only applied if installed.
try:  # pip install truststore
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Default test file (override with DOC_VOLUME_PATH in .env)
DEFAULT_VOLUME_PDF_PATH = (
    "/Volumes/cat_cbt_gaa_brz_dev/default/auditors_supporting_documents/Leander_1/"
    "Law No. 206 of 1960 Amending Law No. 182 of 1960 on Combating Narcotics and "
    "Regulating Their Use and Trade.pdf"
)

TARGET_LANGUAGE = "en"

# Languages we explicitly flag for translation (informational — any non-English is translated).
FLAGGED_LANGUAGES = {"ar": "Arabic", "ur": "Urdu"}

# Azure Text Translator limit is ~50k chars per request; stay well under it.
TRANSLATE_CHUNK_CHARS = 4_500

# Async batch Document Translation
DOC_TRANSLATION_API_VERSION = "2024-05-01"
BLOB_ENDPOINT_SUFFIX = "blob.core.windows.net"
SAS_TTL_HOURS = 2
POLL_INTERVAL_SECONDS = 6
POLL_TIMEOUT_SECONDS = 900

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
logger = logging.getLogger("translate_arabic_doc")

# Quiet the very chatty Azure SDK HTTP logging so translation progress stays readable.
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable '{name}'. "
            f"Add it to your .env file (see the header of this script)."
        )
    return value


def _optional_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# ---------------------------------------------------------------------------
# Databricks Volume download
# ---------------------------------------------------------------------------


def download_volume_file(host: str, token: str, volume_path: str, local_path: Path) -> Path:
    """Download a single file from a Databricks Volume via the Files API."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"{host.rstrip('/')}/api/2.0/fs/files{quote(volume_path, safe='/')}"

    logger.info("[Download] GET %s", volume_path)
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=180)
    resp.raise_for_status()

    local_path.write_bytes(resp.content)
    logger.info("[Download] Saved %s (%d bytes)", local_path.name, len(resp.content))
    return local_path


# ---------------------------------------------------------------------------
# PDF text-layer extraction (no OCR) + language detection
# ---------------------------------------------------------------------------


def extract_pdf_text(pdf_path: Path) -> str:
    """Extract the embedded text layer from every page, in order."""
    doc = fitz.open(str(pdf_path))
    try:
        page_texts: list[str] = []
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            page_texts.append(text)
            logger.info("[Extract] page %d/%d  chars=%d", i, len(doc), len(text))
        content = "\n\n".join(t for t in page_texts if t)
    finally:
        doc.close()

    logger.info("[Extract] total extracted chars=%d", len(content))
    return content.strip()


def detect_language(text_endpoint: str, api_key: str, sample: str, region: str = "") -> str:
    """Detect the language of a text sample via the Translator /detect API. Returns an ISO code."""
    url = f"{text_endpoint.rstrip('/')}/detect"
    headers = {"Ocp-Apim-Subscription-Key": api_key, "Content-Type": "application/json"}
    if region:
        headers["Ocp-Apim-Subscription-Region"] = region
    resp = requests.post(
        url, params={"api-version": "3.0"}, headers=headers,
        json=[{"text": sample[:TRANSLATE_CHUNK_CHARS]}], timeout=60,
    )
    if not resp.ok:
        logger.error("[Detect] HTTP %s: %s", resp.status_code, resp.text[:800])
        logger.error(
            "[Detect] region header sent = %r. A 401 usually means: wrong AZURE_TRANSLATOR_KEY, "
            "missing/incorrect AZURE_TRANSLATOR_REGION, or the .env being loaded is not the one "
            "you edited.", region or "(none)",
        )
    resp.raise_for_status()
    lang = resp.json()[0].get("language", "").lower()
    logger.info("[Detect] detected language = %s", lang or "(unknown)")
    return lang


# ---------------------------------------------------------------------------
# Azure AI Translator — Text Translation (native text-layer fallback)
# ---------------------------------------------------------------------------


def _chunk_text(text: str, max_chars: int) -> list[str]:
    """Split text into <= max_chars chunks, breaking on paragraph boundaries."""
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > max_chars:
            chunks.append(paragraph[:max_chars])
            paragraph = paragraph[max_chars:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


def translate_text(
    endpoint: str, api_key: str, text: str,
    source_lang: str, target_lang: str, region: str = "",
) -> str:
    """Translate text with the Azure AI Translator Text REST API (v3.0)."""
    url = f"{endpoint.rstrip('/')}/translate"
    params = {"api-version": "3.0", "to": target_lang}
    if source_lang:  # omit -> let the Text API auto-detect the source language
        params["from"] = source_lang
    headers = {
        "Ocp-Apim-Subscription-Key": api_key,
        "Content-Type": "application/json",
        "X-ClientTraceId": str(uuid.uuid4()),
    }
    if region:
        headers["Ocp-Apim-Subscription-Region"] = region

    chunks = _chunk_text(text, TRANSLATE_CHUNK_CHARS)
    logger.info("[Translator] Translating %d chunk(s) %s -> %s", len(chunks), source_lang, target_lang)

    translated_parts: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        resp = requests.post(url, params=params, headers=headers, json=[{"text": chunk}], timeout=120)
        if not resp.ok:
            logger.error("[Translator] HTTP %s: %s", resp.status_code, resp.text[:2000])
            resp.raise_for_status()
        payload = resp.json()
        translated_parts.append(payload[0]["translations"][0]["text"])
        logger.info("[Translator] Chunk %d/%d done (%d chars)", i, len(chunks), len(chunk))

    return "\n\n".join(translated_parts)


# ---------------------------------------------------------------------------
# Azure Blob Storage (service-principal SAS or account-key SAS)
# ---------------------------------------------------------------------------


def _blob_clients():
    """Build a BlobServiceClient and a SAS token for the container, using either a
    service principal (user-delegation SAS) or the storage account key."""
    try:
        from azure.storage.blob import (
            BlobServiceClient, ContainerSasPermissions, generate_container_sas,
        )
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "azure-storage-blob is not installed. Run: pip install azure-storage-blob azure-identity"
        ) from exc

    account = _require_env("AZURE_STORAGE_ACCOUNT")
    container = _require_env("AZURE_STORAGE_CONTAINER")
    account_url = f"https://{account}.{BLOB_ENDPOINT_SUFFIX}"

    key = _optional_env("AZURE_STORAGE_KEY")
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    expiry = datetime.now(timezone.utc) + timedelta(hours=SAS_TTL_HOURS)
    permission = ContainerSasPermissions(read=True, write=True, list=True, create=True, delete=True)

    if key:
        logger.info("[Blob] Auth = storage account key")
        bsc = BlobServiceClient(account_url, credential=key)
        sas = generate_container_sas(
            account_name=account, container_name=container, account_key=key,
            permission=permission, start=start, expiry=expiry,
        )
    else:
        logger.info("[Blob] Auth = service principal (user-delegation SAS)")
        try:
            from azure.identity import ClientSecretCredential
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("azure-identity is not installed. Run: pip install azure-identity") from exc
        cred = ClientSecretCredential(
            tenant_id=_require_env("AZURE_TENANT_ID"),
            client_id=_require_env("AZURE_CLIENT_ID"),
            client_secret=_require_env("AZURE_CLIENT_SECRET"),
        )
        bsc = BlobServiceClient(account_url, credential=cred)
        udk = bsc.get_user_delegation_key(start, expiry)
        sas = generate_container_sas(
            account_name=account, container_name=container, user_delegation_key=udk,
            permission=permission, start=start, expiry=expiry,
        )

    return bsc, account, container, sas


# ---------------------------------------------------------------------------
# Async batch Document Translation (via Blob)
# ---------------------------------------------------------------------------


def document_translate_via_blob(
    local_file: Path, doc_endpoint: str, api_key: str, target_lang: str,
) -> Path:
    """Upload -> submit batch translation -> poll -> download translated file. Returns local path."""
    bsc, account, container, sas = _blob_clients()
    account_url = f"https://{account}.{BLOB_ENDPOINT_SUFFIX}"
    job = uuid.uuid4().hex[:12]
    src_prefix = f"doctx/{job}/src"
    tgt_prefix = f"doctx/{job}/tgt"
    src_blob = f"{src_prefix}/{local_file.name}"

    # 1) Upload source
    logger.info("[Blob] Uploading source -> %s/%s", container, src_blob)
    with local_file.open("rb") as fh:
        bsc.get_blob_client(container=container, blob=src_blob).upload_blob(fh, overwrite=True)

    # Storage auth for the Translator service: SAS (default) or the Translator resource's
    # managed identity (no SAS in the URL — requires the Translator MI to hold the
    # 'Storage Blob Data Contributor' role on this storage account).
    storage_auth = _optional_env("AZURE_TRANSLATOR_STORAGE_AUTH", "sas").lower()
    if storage_auth in ("mi", "managed_identity", "managedidentity"):
        logger.info("[DocTranslate] storage auth = managed identity (no SAS in URLs)")
        source_url = f"{account_url}/{container}/{src_prefix}"
        target_url = f"{account_url}/{container}/{tgt_prefix}"
    else:
        logger.info("[DocTranslate] storage auth = SAS")
        source_url = f"{account_url}/{container}/{src_prefix}?{sas}"
        target_url = f"{account_url}/{container}/{tgt_prefix}?{sas}"

    # 2) Submit batch job (omit sourceLanguage -> auto-detect + OCR for scanned PDFs)
    submit_url = f"{doc_endpoint.rstrip('/')}/translator/document/batches"
    body = {
        "inputs": [
            {
                "source": {"sourceUrl": source_url},
                "targets": [{"targetUrl": target_url, "language": target_lang}],
            }
        ]
    }
    logger.info("[DocTranslate] Submitting batch job %s -> %s", job, target_lang)
    resp = requests.post(
        submit_url, params={"api-version": DOC_TRANSLATION_API_VERSION},
        headers={"Ocp-Apim-Subscription-Key": api_key, "Content-Type": "application/json"},
        json=body, timeout=120,
    )
    if not resp.ok:
        logger.error("[DocTranslate] submit HTTP %s: %s", resp.status_code, resp.text[:2000])
        resp.raise_for_status()
    op_location = resp.headers.get("Operation-Location")
    if not op_location:
        raise RuntimeError("No Operation-Location header returned by Document Translation submit.")

    # 3) Poll
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    status = "NotStarted"
    terminal: dict = {}
    while time.time() < deadline:
        pr = requests.get(op_location, headers={"Ocp-Apim-Subscription-Key": api_key}, timeout=60)
        pr.raise_for_status()
        data = pr.json()
        status = data.get("status", "")
        summary = data.get("summary", {})
        logger.info("[DocTranslate] status=%s summary=%s", status, summary)
        if status in ("Succeeded", "Failed", "ValidationFailed", "Cancelled"):
            terminal = data
            break
        time.sleep(POLL_INTERVAL_SECONDS)

    if status != "Succeeded":
        # Surface the batch-level error object (the real reason for ValidationFailed).
        err = terminal.get("error")
        if err:
            logger.error("[DocTranslate] batch error: %s", err)
            code = f"{err.get('code','')}{err.get('innerError',{}).get('code','')}".lower()
            if "accesslevel" in code or "access" in code:
                logger.error(
                    "[Hint] Document Translation could not access the blob with the current SAS. "
                    "This usually means a user-delegation (service-principal) SAS was used. Set "
                    "AZURE_STORAGE_KEY in .env to switch to an account-key SAS, OR ensure the "
                    "service principal has the 'Storage Blob Data Contributor' role (read+list+write) "
                    "and the storage firewall allows trusted Microsoft services."
                )
        # Surface per-document errors, if any documents were registered.
        try:
            dr = requests.get(
                f"{op_location}/documents",
                params={"api-version": DOC_TRANSLATION_API_VERSION},
                headers={"Ocp-Apim-Subscription-Key": api_key}, timeout=60,
            )
            if dr.ok:
                for d in dr.json().get("value", []):
                    if d.get("error"):
                        logger.error(
                            "[DocTranslate] doc error: %s -> %s", d.get("sourcePath"), d.get("error"),
                        )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[DocTranslate] could not fetch per-document errors: %s", exc)
        raise RuntimeError(
            f"Document Translation did not succeed (status={status}). "
            f"See the '[DocTranslate] batch error' line above for the exact cause."
        )

    # 4) Download translated output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    container_client = bsc.get_container_client(container)
    out_path: Path | None = None
    for blob in container_client.list_blobs(name_starts_with=f"{tgt_prefix}/"):
        name = Path(blob.name).name
        if not name:
            continue
        dest = OUTPUT_DIR / f"{local_file.stem}__en{Path(name).suffix or local_file.suffix}"
        logger.info("[Blob] Downloading translated -> %s", dest)
        data = container_client.download_blob(blob.name).readall()
        dest.write_bytes(data)
        out_path = dest

    # 5) Best-effort cleanup of the job's blobs
    try:
        for blob in container_client.list_blobs(name_starts_with=f"doctx/{job}/"):
            container_client.delete_blob(blob.name)
        logger.info("[Blob] Cleaned up job %s", job)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Blob] Cleanup skipped: %s", exc)

    if out_path is None:
        raise RuntimeError("Translation succeeded but no output file was found in the target container.")
    return out_path


# ---------------------------------------------------------------------------
# Azure Document Intelligence — OCR for scanned PDFs (no Blob required)
# ---------------------------------------------------------------------------

DI_MODEL = "prebuilt-read"
DI_API_VERSION_V4 = "2024-11-30"       # Document Intelligence (v4)
DI_API_VERSION_V31 = "2023-07-31"      # Form Recognizer (v3.1) fallback


def ocr_with_document_intelligence(endpoint: str, key: str, file_bytes: bytes) -> str:
    """OCR a (scanned) PDF with Azure Document Intelligence prebuilt-read and return the text.

    Works over the plain REST API using only the resource endpoint + key — no Blob Storage,
    SAS, or extra permissions. Supports Arabic/Urdu print text.
    """
    base = endpoint.rstrip("/")
    headers = {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/pdf"}

    # Try Document Intelligence v4 path; fall back to Form Recognizer v3.1 if 404.
    url = f"{base}/documentintelligence/documentModels/{DI_MODEL}:analyze"
    resp = requests.post(url, params={"api-version": DI_API_VERSION_V4}, headers=headers,
                         data=file_bytes, timeout=180)
    if resp.status_code == 404:
        url = f"{base}/formrecognizer/documentModels/{DI_MODEL}:analyze"
        resp = requests.post(url, params={"api-version": DI_API_VERSION_V31}, headers=headers,
                             data=file_bytes, timeout=180)
    if not resp.ok:
        logger.error("[OCR] submit HTTP %s: %s", resp.status_code, resp.text[:2000])
        resp.raise_for_status()

    op_location = resp.headers.get("Operation-Location")
    if not op_location:
        raise RuntimeError("Document Intelligence did not return an Operation-Location header.")

    deadline = time.time() + POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        pr = requests.get(op_location, headers={"Ocp-Apim-Subscription-Key": key}, timeout=60)
        pr.raise_for_status()
        data = pr.json()
        status = (data.get("status") or "").lower()
        logger.info("[OCR] status=%s", status)
        if status == "succeeded":
            return (data.get("analyzeResult", {}).get("content", "") or "").strip()
        if status in ("failed", "canceled", "cancelled"):
            raise RuntimeError(f"Document Intelligence OCR {status}: {data.get('error')}")
        time.sleep(3)
    raise RuntimeError("Document Intelligence OCR timed out.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    load_dotenv()

    databricks_host = _require_env("DATABRICKS_HOST")
    databricks_token = _require_env("DATABRICKS_TOKEN")
    translator_text_endpoint = _require_env("AZURE_TRANSLATOR_TEXT_ENDPOINT")
    translator_key = _require_env("AZURE_TRANSLATOR_KEY")
    translator_region = _optional_env("AZURE_TRANSLATOR_REGION")

    # The Text Translation v3.0 API (/translate, /detect) is served ONLY by the global
    # endpoint — the resource custom-domain (*.cognitiveservices.azure.com) returns 404.
    GLOBAL_TEXT_ENDPOINT = "https://api.cognitive.microsofttranslator.com"
    if "microsofttranslator.com" not in translator_text_endpoint:
        logger.warning(
            "[Translator] '%s' is not the Text Translation endpoint — overriding with the "
            "global endpoint %s. Set AZURE_TRANSLATOR_REGION to your resource region.",
            translator_text_endpoint, GLOBAL_TEXT_ENDPOINT,
        )
        translator_text_endpoint = GLOBAL_TEXT_ENDPOINT
    if not translator_region:
        logger.warning(
            "[Translator] AZURE_TRANSLATOR_REGION is not set. The global endpoint with a "
            "regional key usually requires it — if you get 401/403, set the region."
        )
    # Show exactly what is loaded so a wrong/misplaced .env is obvious.
    logger.info(
        "[Translator] Using text endpoint=%s | region=%s | key set=%s (…%s)",
        translator_text_endpoint, translator_region or "(NOT SET)",
        bool(translator_key), translator_key[-4:] if translator_key else "",
    )
    logger.info("[Env] .env loaded from CWD=%s", os.getcwd())
    doc_endpoint = _optional_env("AZURE_TRANSLATOR_DOCUMENT_ENDPOINT")

    volume_path = _optional_env("DOC_VOLUME_PATH") or DEFAULT_VOLUME_PDF_PATH

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    source_name = Path(volume_path).name
    file_stem = Path(volume_path).stem

    with tempfile.TemporaryDirectory(prefix="doc_translate_") as tmp:
        local_pdf = Path(tmp) / source_name

        # 1) Download from the Databricks Volume
        download_volume_file(databricks_host, databricks_token, volume_path, local_pdf)

        # 2) Extract text layer + flag language
        text = extract_pdf_text(local_pdf)

        # 3) If no text layer (scanned), OCR with Azure Document Intelligence (no Blob needed)
        if not text:
            di_endpoint = _require_env("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
            di_key = _require_env("AZURE_DOCUMENT_INTELLIGENCE_KEY")
            logger.info(
                "[Route] No text layer (scanned PDF) — running OCR via Azure Document "
                "Intelligence (prebuilt-read)."
            )
            text = ocr_with_document_intelligence(di_endpoint, di_key, local_pdf.read_bytes())
            logger.info("[OCR] extracted %d chars", len(text))
            if not text:
                logger.error("OCR produced no text — cannot translate.")
                return 2
            (OUTPUT_DIR / f"{file_stem}__ocr_source.txt").write_text(text, encoding="utf-8")

        # 4) Flag the language
        detected = detect_language(translator_text_endpoint, translator_key, text, translator_region)
        if detected == "en":
            logger.info("[Route] Document is English — no translation needed.")
            (OUTPUT_DIR / f"{file_stem}__english.txt").write_text(text, encoding="utf-8")
            logger.info("[Output] Wrote English text -> %s", OUTPUT_DIR / f"{file_stem}__english.txt")
            return 0

        flag = FLAGGED_LANGUAGES.get(detected)
        logger.info(
            "[Route] Non-English document flagged (lang=%s%s) — translating via Text API.",
            detected, f", {flag}" if flag else "",
        )

        # 5) Translate to English via the Text Translation API (works on OCR'd or native text)
        english_text = translate_text(
            translator_text_endpoint, translator_key, text,
            detected, TARGET_LANGUAGE, region=translator_region,
        )

    english_out = OUTPUT_DIR / f"{file_stem}__english.txt"
    english_out.write_text(english_text, encoding="utf-8")
    logger.info("[Output] Wrote translated English text -> %s", english_out)
    logger.info("Done. The English text is ready to feed into the ingestion pipeline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
