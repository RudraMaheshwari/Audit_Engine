# Databricks notebook — Azure Translator test (run INSIDE Databricks; no pip install needed)
# ----------------------------------------------------------------------------------------------
# The cluster has no PyPI access, so this extracts .docx with the STANDARD LIBRARY only (a .docx is a
# zip of XML) and uses whatever PDF library is already on the cluster (pymupdf/fitz or pdfplumber).
# `requests` is already available on Databricks. Paste this whole thing into one Python cell and run.
# ----------------------------------------------------------------------------------------------

import html
import re
import uuid
import zipfile
from pathlib import Path

import requests

# ================== CONFIG — set these ==================
# Option A (recommended): pull the key from the secret scope. List names with: dbutils.secrets.list("cpr-audit")
try:
    AZURE_TRANSLATOR_KEY = dbutils.secrets.get(scope="cpr-audit", key="translator-key")  # <-- adjust key name
except Exception:
    AZURE_TRANSLATOR_KEY = ""   # Option B: paste the key directly ->  AZURE_TRANSLATOR_KEY = "xxxxxxxx"

TRANSLATOR_ENDPOINT = ""   # set your resource endpoint, e.g. https://<resource>.cognitiveservices.azure.com
TRANSLATOR_REGION   = "eastus"
# Endpoints tried IN ORDER. The custom resource host may be unreachable (private endpoint / no DNS) on
# a locked-down cluster; the GLOBAL host is a standard Azure service many clusters can still reach with
# the resource key + region header. First one that connects + authorizes wins.
GLOBAL_ENDPOINT = "https://api.cognitive.microsofttranslator.com"

FILES = [
    "/Volumes/nbod_gaagd/cpr_risk_analysis/test/DB_JJMCプライシング関係承認権限規定（日本語）.docx",
    "/Volumes/nbod_gaagd/cpr_risk_analysis/test/DB_JJMCプライシング関係承認権限規定（日本語）.pdf",
]

API_VERSION = "3.0"
TARGET = "en"
CHUNK_CHARS = 45_000   # stay under Azure's ~50k chars/request limit
# ========================================================


def _text_base(endpoint: str) -> str:
    e = endpoint.strip().rstrip("/")
    host = e.split("://", 1)[-1].split("/", 1)[0].lower()
    if host.endswith("cognitiveservices.azure.com") and "/translator/text" not in e:
        return e + "/translator/text/v3.0"
    return e


def extract_docx(path: Path) -> str:
    """Pure-stdlib .docx text: a .docx is a zip; the body is word/document.xml. We keep <w:t> text and
    turn </w:p> (paragraph end) and <w:tab/> into newlines/spaces. No python-docx needed."""
    with zipfile.ZipFile(str(path)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    out = []
    # NOTE: `<w:t(?:\s[^>]*)?>` matches <w:t> and <w:t attr…> but NOT <w:tab/> (no space/">" after w:t).
    for m in re.finditer(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>|</w:p>|<w:tab[^>]*/>", xml, flags=re.DOTALL):
        if m.group(0) == "</w:p>":
            out.append("\n")
        elif m.group(0).startswith("<w:tab"):
            out.append(" ")
        else:
            out.append(html.unescape(m.group(1) or ""))
    # collapse excess blank lines
    text = re.sub(r"\n{3,}", "\n\n", "".join(out))
    return text.strip()


def extract_pdf(path: Path) -> str:
    """Use whatever PDF library is already on the cluster (no install)."""
    try:
        import fitz  # pymupdf
        doc = fitz.open(str(path))
        try:
            return "\n\n".join((page.get_text() or "") for page in doc).strip()
        finally:
            doc.close()
    except ImportError:
        pass
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            return "\n\n".join((p.extract_text() or "") for p in pdf.pages).strip()
    except ImportError:
        print("   ⚠ No PDF library (pymupdf/pdfplumber) on this cluster — skipping the PDF; the .docx "
              "test still proves the Translator works.")
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
    """Return the first endpoint whose host resolves + accepts a tiny translate call. Prefers the
    custom resource host, then the global Azure host. Raises if none is reachable."""
    import socket

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
            r = requests.post(f"{base}/translate",
                              params={"api-version": API_VERSION, "to": TARGET},
                              headers=headers, json=[{"text": "テスト"}], timeout=30)
            if r.ok:
                print(f"   ✓ using {host} (resolved {ip})")
                return base
            print(f"   ✗ {host} resolved ({ip}) but HTTP {r.status_code}: {r.text[:200]}; trying next")
        except Exception as exc:  # noqa: BLE001
            print(f"   ✗ {host} resolved ({ip}) but connection failed ({exc}); trying next")
    raise RuntimeError("No Translator endpoint is reachable from this cluster (DNS/firewall). "
                       "Run on the cluster your team uses, or get this cluster's network access.")


def translate_to_english(text: str, base: str) -> tuple:
    url = f"{base}/translate"
    params = {"api-version": API_VERSION, "to": TARGET}   # no 'from' -> auto-detect
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
    except Exception as exc:
        print(f"   FAIL — extraction: {exc}"); return False
    if not source.strip():
        print("   (no text extracted — skipped)"); return False
    print(f"   extracted {len(source):,} chars")
    try:
        english, detected = translate_to_english(source, base)
    except Exception as exc:
        print(f"   FAIL — translation: {exc}"); return False
    out_path = path.with_suffix(path.suffix + ".en.txt")
    try:
        out_path.write_text(english, encoding="utf-8")
        print(f"   saved -> {out_path}")
    except Exception as exc:
        print(f"   ⚠ could not write output ({exc}) — preview only")
    print(f"   detected source language = {detected or '(unknown)'}")
    print(f"   english length = {len(english):,} chars")
    print(f"\n   --- ENGLISH PREVIEW (first 600 chars) ---\n   {english.strip()[:600]}")
    return True


# ---- run ----
if not AZURE_TRANSLATOR_KEY:
    print("Set AZURE_TRANSLATOR_KEY (from secret scope 'cpr-audit' or paste it).")
else:
    print(f"region = {TRANSLATOR_REGION} | selecting a reachable endpoint…")
    try:
        base = _pick_endpoint()
    except Exception as exc:
        print(f"\nNO REACHABLE ENDPOINT — {exc}")
        base = None
    if base:
        results = {Path(p).name: process(Path(p), base) for p in FILES}
        print("\n" + "=" * 70 + "\nRESULTS\n" + "=" * 70)
        for name, ok in results.items():
            print(f"   {'PASS' if ok else 'FAIL'}  {name}")
