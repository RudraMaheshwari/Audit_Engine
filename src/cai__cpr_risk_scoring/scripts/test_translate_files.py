"""End-to-end translation test on REAL files — extract text (like ingestion) then Azure-translate to
English (auto-detect source, so it is language-agnostic: Japanese, Korean, anything).

Mirrors the ingestion extraction path:
  * .docx -> python-docx paragraphs + table cells (same as src/ingestion/extractors/word_extractor.py)
  * .pdf  -> pdfplumber text layer per page (same first step as src/ingestion/extractors/pdf_extractor.py)
            NOTE: a computer-written PDF has a real text layer, so this works without OCR. If a PDF is
            a SCANNED image (no text layer) this prints a warning — that needs the OCR path, not here.

For each file it: extracts -> detects language -> translates to English (chunked under the 50k limit)
-> writes "<original>.en.txt" next to the source -> prints a preview.

Setup:
  pip install requests python-dotenv python-docx pdfplumber
  # optional on corporate networks (TLS interception): pip install truststore
  # .env (same names as scripts/translate_arabic_doc.py):
  #   AZURE_TRANSLATOR_TEXT_ENDPOINT=https://api.cognitive.microsofttranslator.com
  #   AZURE_TRANSLATOR_KEY=<key>
  #   AZURE_TRANSLATOR_REGION=<resource region, e.g. eastus>   # required for a REGIONAL resource
Run:
  python test_translate_files.py                       # uses the two DEFAULT_FILES below
  python test_translate_files.py "C:/path/a.docx" "C:/path/b.pdf"   # or pass your own
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv

# Corporate TLS interception -> make Python trust the OS cert store. Best-effort.
try:  # pip install truststore
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

load_dotenv()

API_VERSION = "3.0"
TARGET = "en"
CHUNK_CHARS = 45_000  # Azure Text Translator limit is ~50k chars/request — stay under it.


def _text_base(endpoint: str) -> str:
    """Resolve the correct Text Translation base URL for either endpoint style:
      * Global / regional host  (api[-xxx].cognitive.microsofttranslator.com)  -> serves /translate at root.
      * Custom subdomain resource (<name>.cognitiveservices.azure.com)          -> Text Translation lives
        under /translator/text/v3.0, so /translate at the root returns 404.
    """
    e = endpoint.strip().strip('"').strip("'").rstrip("/")
    host = e.split("://", 1)[-1].split("/", 1)[0].lower()
    if host.endswith("cognitiveservices.azure.com") and "/translator/text" not in e:
        return e + "/translator/text/v3.0"
    return e

# The two files under test (override by passing paths on the command line).
DEFAULT_FILES = [
    r"C:\Users\RMahesh8\Downloads\DB_JJMCプライシング関係承認権限規定（日本語）.docx",
    r"C:\Users\RMahesh8\Downloads\DB_JJMCプライシング関係承認権限規定（日本語）.pdf",
]


# ── Extraction (faithful to the ingestion extractors) ───────────────────────────────────────────
def extract_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    parts: list[str] = []
    for para in doc.paragraphs:
        t = para.text.strip()
        if t:
            parts.append(t)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n\n".join(parts)


def extract_pdf(path: Path) -> str:
    import pdfplumber

    pages: list[str] = []
    empty = 0
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            txt = (page.extract_text() or "").strip()
            if txt:
                pages.append(txt)
            else:
                empty += 1
    if empty:
        print(f"   ⚠ {empty} page(s) had NO text layer — those are scanned images and would need the "
              f"OCR path (Azure Document Intelligence), not covered by this quick test.")
    return "\n\n".join(pages)


def extract(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return extract_docx(path)
    if suffix == ".pdf":
        return extract_pdf(path)
    raise ValueError(f"Unsupported file type for this test: {suffix} ({path.name})")


# ── Azure Translator (Text v3.0) — auto-detect source, translate to English ──────────────────────
def _headers(key: str, region: str) -> dict:
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/json",
        "X-ClientTraceId": str(uuid.uuid4()),
    }
    if region:
        headers["Ocp-Apim-Subscription-Region"] = region
    return headers


def _chunk(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(para) > max_chars:
            chunks.append(para[:max_chars])
            para = para[max_chars:]
        current = para
    if current:
        chunks.append(current)
    return chunks


def translate_to_english(text: str, endpoint: str, key: str, region: str) -> tuple[str, str]:
    """Return (english_text, detected_language). Auto-detect source (no 'from')."""
    url = f"{_text_base(endpoint)}/translate"
    params = {"api-version": API_VERSION, "to": TARGET}  # no 'from' -> auto-detect
    chunks = _chunk(text, CHUNK_CHARS)
    out: list[str] = []
    detected = ""
    for i, chunk in enumerate(chunks, start=1):
        resp = requests.post(url, params=params, headers=_headers(key, region),
                             json=[{"text": chunk}], timeout=120)
        if not resp.ok:
            if resp.status_code in (401, 403):
                print(f"   ↳ HTTP {resp.status_code}: wrong AZURE_TRANSLATOR_KEY or missing/incorrect "
                      f"AZURE_TRANSLATOR_REGION (sent={region or '(none)'}).")
            resp.raise_for_status()
        payload = resp.json()[0]
        detected = detected or (payload.get("detectedLanguage") or {}).get("language", "")
        out.append(payload["translations"][0]["text"])
        print(f"   chunk {i}/{len(chunks)} translated ({len(chunk)} chars)")
    return "\n\n".join(out), detected


def process(path: Path, endpoint: str, key: str, region: str) -> bool:
    print("\n" + "=" * 78 + f"\nFILE: {path.name}\n" + "=" * 78)
    if not path.exists():
        print(f"   FAIL — file not found: {path}")
        return False
    try:
        source = extract(path)
    except Exception as exc:  # noqa: BLE001
        print(f"   FAIL — extraction error: {exc}")
        return False
    if not source.strip():
        print("   FAIL — no text extracted (empty, or a scanned PDF needing OCR).")
        return False
    print(f"   extracted {len(source):,} chars")
    try:
        english, detected = translate_to_english(source, endpoint, key, region)
    except Exception as exc:  # noqa: BLE001
        print(f"   FAIL — translation error: {exc}")
        return False
    out_path = path.with_suffix(path.suffix + ".en.txt")
    try:
        out_path.write_text(english, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"   ⚠ could not write output file: {exc}")
    print(f"   detected source language = {detected or '(unknown)'}")
    print(f"   english length = {len(english):,} chars | saved -> {out_path.name}")
    preview = english.strip().replace("\n", " ")[:600]
    print(f"\n   --- ENGLISH PREVIEW (first 600 chars) ---\n   {preview}")
    return True


def main() -> int:
    endpoint = (os.getenv("AZURE_TRANSLATOR_TEXT_ENDPOINT") or "").strip().strip('"').strip("'").rstrip("/")
    key = (os.getenv("AZURE_TRANSLATOR_KEY") or "").strip().strip('"').strip("'")
    region = (os.getenv("AZURE_TRANSLATOR_REGION") or "").strip().strip('"').strip("'")
    if not endpoint or not key:
        print("FAIL — set AZURE_TRANSLATOR_TEXT_ENDPOINT and AZURE_TRANSLATOR_KEY in .env")
        return 1
    print(f"endpoint   = {endpoint} | region = {region or '(not set)'} | key = {len(key)} chars")
    print(f"request URL -> {_text_base(endpoint)}/translate?api-version={API_VERSION}&to={TARGET}")

    files = [Path(p) for p in (sys.argv[1:] or DEFAULT_FILES)]
    results = {p.name: process(p, endpoint, key, region) for p in files}

    print("\n" + "=" * 78 + "\nRESULTS\n" + "=" * 78)
    for name, ok in results.items():
        print(f"   {'PASS' if ok else 'FAIL'}  {name}")
    all_ok = all(results.values())
    print("\n" + ("ALL FILES TRANSLATED — open the .en.txt files to eyeball quality."
                  if all_ok else "SOME FILES FAILED — see messages above."))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
