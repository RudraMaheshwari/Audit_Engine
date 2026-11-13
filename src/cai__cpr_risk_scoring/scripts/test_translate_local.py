"""LOCAL Azure Translator test (run from VS Code) — uses LOCAL file paths.

Extracts .docx with the standard library (no install) and .pdf with pdfplumber/pymupdf (pip install
locally). Translates to English via Azure Translator, trying the custom resource host first then the
GLOBAL host. NOTE: from a local machine the private resource host is usually firewall-blocked, so this
succeeds only if the GLOBAL host (api.cognitive.microsofttranslator.com) is reachable from your network.

Setup:
  pip install requests python-dotenv pdfplumber      # pdfplumber only needed for PDFs
  # optional on corporate networks: pip install truststore
  # .env:
  #   AZURE_TRANSLATOR_KEY=<key>            (or paste below)
  #   AZURE_TRANSLATOR_REGION=eastus
  #   AZURE_TRANSLATOR_ENDPOINT=https://<your-resource>.cognitiveservices.azure.com
Run:
  python test_translate_local.py                                  # uses DEFAULT_FILES below
  python test_translate_local.py "C:/path/a.docx" "C:/path/b.pdf" # or pass your own local paths
"""

from __future__ import annotations

import html
import os
import re
import socket
import sys
import uuid
import zipfile
from pathlib import Path

import requests
from dotenv import load_dotenv

try:  # corporate TLS interception -> trust the OS cert store
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

load_dotenv()

# ================== CONFIG ==================
AZURE_TRANSLATOR_KEY = (os.getenv("AZURE_TRANSLATOR_KEY") or "").strip() or "PASTE_KEY_HERE"
TRANSLATOR_REGION    = (os.getenv("AZURE_TRANSLATOR_REGION") or "eastus").strip()
TRANSLATOR_ENDPOINT  = (os.getenv("AZURE_TRANSLATOR_ENDPOINT") or "").strip()   # set via .env
GLOBAL_ENDPOINT      = "https://api.cognitive.microsofttranslator.com"

# Edit these to your LOCAL files (or pass paths on the command line).
DEFAULT_FILES = [
    r"C:\Users\RMahesh8\Downloads\DB_JJMCプライシング関係承認権限規定（日本語）.docx",
    r"C:\Users\RMahesh8\Downloads\DB_JJMCプライシング関係承認権限規定（日本語）.pdf",
]

API_VERSION = "3.0"
TARGET = "en"
CHUNK_CHARS = 45_000
# ===========================================


def _text_base(endpoint: str) -> str:
    e = endpoint.strip().rstrip("/")
    host = e.split("://", 1)[-1].split("/", 1)[0].lower()
    if host.endswith("cognitiveservices.azure.com") and "/translator/text" not in e:
        return e + "/translator/text/v3.0"
    return e


def extract_docx(path: Path) -> str:
    with zipfile.ZipFile(str(path)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    out = []
    for m in re.finditer(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>|</w:p>|<w:tab[^>]*/>", xml, flags=re.DOTALL):
        if m.group(0) == "</w:p>":
            out.append("\n")
        elif m.group(0).startswith("<w:tab"):
            out.append(" ")
        else:
            out.append(html.unescape(m.group(1) or ""))
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


def extract_pdf(path: Path) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            return "\n\n".join((p.extract_text() or "") for p in pdf.pages).strip()
    except ImportError:
        pass
    try:
        import fitz  # pymupdf
        doc = fitz.open(str(path))
        try:
            return "\n\n".join((page.get_text() or "") for page in doc).strip()
        finally:
            doc.close()
    except ImportError:
        print("   ⚠ No PDF library — `pip install pdfplumber` to test PDFs (the .docx test still runs).")
        return ""


def extract(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return extract_docx(path)
    if suffix == ".pdf":
        return extract_pdf(path)
    raise ValueError(f"Unsupported file type: {suffix} ({path.name})")


def _chunk(text: str, max_chars: int) -> list:
    chunks, current = [], ""
    for para in text.split("\n\n"):
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(para) > max_chars:
            chunks.append(para[:max_chars]); para = para[max_chars:]
        current = para
    if current:
        chunks.append(current)
    return chunks


def _pick_endpoint() -> str:
    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_TRANSLATOR_KEY,
        "Ocp-Apim-Subscription-Region": TRANSLATOR_REGION,
        "Content-Type": "application/json",
    }
    for ep in (TRANSLATOR_ENDPOINT, GLOBAL_ENDPOINT):
        base = _text_base(ep)
        host = base.split("://", 1)[-1].split("/", 1)[0]
        try:
            ip = socket.gethostbyname(host)
        except Exception as exc:  # noqa: BLE001
            print(f"   ✗ {host} — DNS cannot resolve ({exc}); trying next")
            continue
        try:
            r = requests.post(f"{base}/translate", params={"api-version": API_VERSION, "to": TARGET},
                              headers=headers, json=[{"text": "テスト"}], timeout=30)
            if r.ok:
                print(f"   ✓ using {host} (resolved {ip})")
                return base
            print(f"   ✗ {host} resolved ({ip}) but HTTP {r.status_code}: {r.text[:200]}; trying next")
        except Exception as exc:  # noqa: BLE001
            print(f"   ✗ {host} resolved ({ip}) but connection failed ({exc}); trying next")
    raise RuntimeError("No Translator endpoint reachable from this machine. The private host is likely "
                       "firewall-blocked locally, and the global host didn't accept the request — run on "
                       "Databricks, or get your IP allowlisted on the Translator resource.")


def translate_to_english(text: str, base: str) -> tuple:
    url = f"{base}/translate"
    params = {"api-version": API_VERSION, "to": TARGET}
    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_TRANSLATOR_KEY,
        "Ocp-Apim-Subscription-Region": TRANSLATOR_REGION,
        "Content-Type": "application/json",
        "X-ClientTraceId": str(uuid.uuid4()),
    }
    out, detected = [], ""
    chunks = _chunk(text, CHUNK_CHARS)
    for i, chunk in enumerate(chunks, start=1):
        resp = requests.post(url, params=params, headers=headers, json=[{"text": chunk}], timeout=120)
        if not resp.ok:
            print(f"   ↳ HTTP {resp.status_code}: {resp.text[:300]}")
            resp.raise_for_status()
        payload = resp.json()[0]
        detected = detected or (payload.get("detectedLanguage") or {}).get("language", "")
        out.append(payload["translations"][0]["text"])
        print(f"   chunk {i}/{len(chunks)} translated ({len(chunk)} chars)")
    return "\n\n".join(out), detected


def process(path: Path, base: str) -> bool:
    print("\n" + "=" * 70 + f"\nFILE: {path.name}\n" + "=" * 70)
    if not path.exists():
        print(f"   FAIL — not found: {path}"); return False
    try:
        source = extract(path)
    except Exception as exc:  # noqa: BLE001
        print(f"   FAIL — extraction: {exc}"); return False
    if not source.strip():
        print("   (no text extracted — skipped)"); return False
    print(f"   extracted {len(source):,} chars")
    try:
        english, detected = translate_to_english(source, base)
    except Exception as exc:  # noqa: BLE001
        print(f"   FAIL — translation: {exc}"); return False
    out_path = path.with_suffix(path.suffix + ".en.txt")
    try:
        out_path.write_text(english, encoding="utf-8")
        print(f"   saved -> {out_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"   ⚠ could not write output ({exc}) — preview only")
    print(f"   detected source language = {detected or '(unknown)'}")
    print(f"   english length = {len(english):,} chars")
    print(f"\n   --- ENGLISH PREVIEW (first 600 chars) ---\n   {english.strip()[:600]}")
    return True


def main() -> int:
    if not AZURE_TRANSLATOR_KEY or AZURE_TRANSLATOR_KEY == "PASTE_KEY_HERE":
        print("Set AZURE_TRANSLATOR_KEY in .env (or paste it in the script)."); return 1
    print(f"region = {TRANSLATOR_REGION} | selecting a reachable endpoint…")
    try:
        base = _pick_endpoint()
    except Exception as exc:  # noqa: BLE001
        print(f"\nNO REACHABLE ENDPOINT — {exc}"); return 1
    files = [Path(p) for p in (sys.argv[1:] or DEFAULT_FILES)]
    results = {p.name: process(p, base) for p in files}
    print("\n" + "=" * 70 + "\nRESULTS\n" + "=" * 70)
    for name, ok in results.items():
        print(f"   {'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
