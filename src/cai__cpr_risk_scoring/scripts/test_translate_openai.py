"""End-to-end translation test on REAL files using AZURE OPENAI (the LLM you already have configured)
instead of Azure Translator — no extra resource/key needed.

Language-AGNOSTIC: the prompt just says "translate to English", so Japanese, Korean, Arabic, anything
is auto-handled. Mirrors the ingestion extraction path, then translates each chunk with Azure OpenAI.

Flow per file: extract text (like ingestion) -> chunk -> translate each chunk to English with the LLM
-> write "<original>.en.txt" next to the source -> print a preview.

Setup:
  pip install langchain-openai python-docx pdfplumber python-dotenv
  # optional on corporate networks: pip install truststore
  # .env (same names the app already uses):
  #   AZURE_OPENAI_ENDPOINT=https://<your-gateway>/
  #   AZURE_OPENAI_API_KEY=<key>
  #   AZURE_OPENAI_DEPLOYMENT=gpt-5
  #   AZURE_OPENAI_API_VERSION=2024-10-21
Run:
  python test_translate_openai.py                       # uses the two DEFAULT_FILES below
  python test_translate_openai.py "C:/path/a.docx" "C:/path/b.pdf"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

try:  # corporate TLS interception -> trust the OS cert store
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

load_dotenv()

# Keep each LLM call comfortably within context + output limits; translation output ~ input length.
CHUNK_CHARS = 6_000

DEFAULT_FILES = [
    r"C:\Users\RMahesh8\Downloads\DB_JJMCプライシング関係承認権限規定（日本語）.docx",
    r"C:\Users\RMahesh8\Downloads\DB_JJMCプライシング関係承認権限規定（日本語）.pdf",
]

SYSTEM_PROMPT = (
    "You are a professional translator for a corporate compliance audit. Translate the user's text "
    "into clear, natural English. Rules: (1) auto-detect the source language — it may be Japanese, "
    "Korean, or any language; (2) translate EVERYTHING, do not summarise or omit; (3) preserve the "
    "structure — keep line breaks and keep table rows written with ' | ' separators as-is; (4) keep "
    "numbers, dates, monetary amounts, and proper nouns (people, companies, systems) accurate; "
    "(5) output ONLY the English translation — no notes, no preamble, no explanations."
)


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
        print(f"   ⚠ {empty} page(s) had NO text layer — scanned images, would need the OCR path.")
    return "\n\n".join(pages)


def extract(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return extract_docx(path)
    if suffix == ".pdf":
        return extract_pdf(path)
    raise ValueError(f"Unsupported file type: {suffix} ({path.name})")


# ── Azure OpenAI translation ─────────────────────────────────────────────────────────────────────
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


def build_client():
    from langchain_openai import AzureChatOpenAI

    return AzureChatOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        max_retries=3,
    )


def translate_to_english(llm, text: str) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    chunks = _chunk(text, CHUNK_CHARS)
    out: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        resp = llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=chunk)])
        out.append((resp.content or "").strip())
        print(f"   chunk {i}/{len(chunks)} translated ({len(chunk)} chars)")
    return "\n\n".join(out)


def process(path: Path, llm) -> bool:
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
        english = translate_to_english(llm, source)
    except Exception as exc:  # noqa: BLE001
        print(f"   FAIL — translation error: {exc}")
        return False
    out_path = path.with_suffix(path.suffix + ".en.txt")
    try:
        out_path.write_text(english, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"   ⚠ could not write output file: {exc}")
    print(f"   english length = {len(english):,} chars | saved -> {out_path.name}")
    preview = english.strip().replace("\n", " ")[:600]
    print(f"\n   --- ENGLISH PREVIEW (first 600 chars) ---\n   {preview}")
    return True


def main() -> int:
    if not os.getenv("AZURE_OPENAI_ENDPOINT") or not os.getenv("AZURE_OPENAI_API_KEY"):
        print("FAIL — set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY in .env")
        return 1
    print(f"endpoint   = {os.getenv('AZURE_OPENAI_ENDPOINT')}")
    print(f"deployment = {os.getenv('AZURE_OPENAI_DEPLOYMENT', 'gpt-5')} | "
          f"api_version = {os.getenv('AZURE_OPENAI_API_VERSION', '2024-10-21')}")

    try:
        llm = build_client()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL — could not build Azure OpenAI client: {exc}")
        return 1

    files = [Path(p) for p in (sys.argv[1:] or DEFAULT_FILES)]
    results = {p.name: process(p, llm) for p in files}

    print("\n" + "=" * 78 + "\nRESULTS\n" + "=" * 78)
    for name, ok in results.items():
        print(f"   {'PASS' if ok else 'FAIL'}  {name}")
    all_ok = all(results.values())
    print("\n" + ("ALL FILES TRANSLATED — open the .en.txt files to eyeball quality."
                  if all_ok else "SOME FILES FAILED — see messages above."))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
