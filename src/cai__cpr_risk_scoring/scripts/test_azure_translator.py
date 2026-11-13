"""Verify the Azure AI Translator (Text Translation v3.0) credentials the FCPA Audit ingestion will
use to convert NON-ENGLISH supporting docs to English — BEFORE wiring translation into the pipeline.

Language-AGNOSTIC: nothing is hard-coded to Japanese/Korean/Arabic. Every check uses AUTO-DETECT
(no `from` language) and target English, so it proves the same path works for ANY source language.

Checks (each isolated — one failure does not stop the others; a pass/fail matrix prints at the end):
  1. Config            — AZURE_TRANSLATOR_TEXT_ENDPOINT + AZURE_TRANSLATOR_KEY present
  2. Connectivity      — GET /languages?scope=translation (no key needed; proves the endpoint/TLS work)
  3. Detect            — POST /detect auto-detects each multi-language sample's ISO code
  4. Translate->EN     — POST /translate auto-detect -> English for Japanese, Korean (+ Arabic) samples

Setup:
  pip install requests python-dotenv
  # optional, for corporate TLS interception: pip install truststore
  # .env (names MATCH scripts/translate_arabic_doc.py):
  #   AZURE_TRANSLATOR_TEXT_ENDPOINT=https://api.cognitive.microsofttranslator.com
  #   AZURE_TRANSLATOR_KEY=<your key>
  #   AZURE_TRANSLATOR_REGION=<your resource region, e.g. eastus>   # required for a REGIONAL resource
Run:
  python test_azure_translator.py
"""

from __future__ import annotations

import os
import sys
import uuid

import requests
from dotenv import load_dotenv

# Corporate networks often TLS-intercept api.cognitive.microsofttranslator.com with a root CA that
# Python's bundled certifi does not trust (CERTIFICATE_VERIFY_FAILED). truststore makes Python use the
# OS certificate store (which already trusts the corporate CA). Best-effort — only if installed.
try:  # pip install truststore
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

load_dotenv()

API_VERSION = "3.0"
TARGET = "en"

# Multi-language samples — deliberately NOT one language. Each is a short, audit-flavoured sentence.
# (text, human label for the console). Detection/translation must work on all of them via auto-detect.
SAMPLES: list[tuple[str, str]] = [
    ("これはテスト文書です。支払いは第三者仲介業者に対して行われました。", "Japanese"),
    ("이것은 테스트 문서입니다. 제3자 중개인에게 대금이 지급되었습니다.", "Korean"),
    ("هذه وثيقة اختبار. تم الدفع إلى وسيط طرف ثالث.", "Arabic"),
]


def _h(title: str) -> None:
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def _headers(key: str, region: str) -> dict:
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/json",
        "X-ClientTraceId": str(uuid.uuid4()),
    }
    if region:
        # Required when the key belongs to a REGIONAL Translator resource (not the global key).
        headers["Ocp-Apim-Subscription-Region"] = region
    return headers


def _auth_hint(resp: requests.Response, region: str) -> None:
    if resp.status_code in (401, 403):
        print(
            f"   ↳ HTTP {resp.status_code}. Usually: wrong AZURE_TRANSLATOR_KEY, or a REGIONAL resource "
            f"with a missing/incorrect AZURE_TRANSLATOR_REGION (sent={region or '(none)'}), or the "
            f".env loaded is not the one you edited."
        )


def check_config() -> tuple[bool, dict]:
    _h("1. Config — endpoint + key present")
    endpoint = (os.getenv("AZURE_TRANSLATOR_TEXT_ENDPOINT") or "").strip().rstrip("/")
    key = (os.getenv("AZURE_TRANSLATOR_KEY") or "").strip()
    region = (os.getenv("AZURE_TRANSLATOR_REGION") or "").strip()
    print(f"   endpoint = {endpoint or '(missing)'}")
    print(f"   key      = {'set (' + str(len(key)) + ' chars)' if key else '(missing)'}")
    print(f"   region   = {region or '(not set — ok only for the GLOBAL key)'}")
    ok = bool(endpoint and key)
    if not ok:
        print("   FAIL — set AZURE_TRANSLATOR_TEXT_ENDPOINT and AZURE_TRANSLATOR_KEY in .env")
    else:
        print("   PASS")
    return ok, {"endpoint": endpoint, "key": key, "region": region}


def check_connectivity(cfg: dict) -> bool:
    _h("2. Connectivity — GET /languages?scope=translation (no key)")
    try:
        resp = requests.get(
            f"{cfg['endpoint']}/languages",
            params={"api-version": API_VERSION, "scope": "translation"},
            timeout=60,
        )
        resp.raise_for_status()
        langs = resp.json().get("translation", {})
        has_en = "en" in langs
        print(f"   reachable — {len(langs)} translatable languages advertised | English supported = {has_en}")
        # Spot-check that the languages we care about are actually offered.
        for code, name in (("ja", "Japanese"), ("ko", "Korean"), ("ar", "Arabic")):
            print(f"     {code} ({name}) offered = {code in langs}")
        print("   PASS" if has_en else "   FAIL — target language 'en' not advertised")
        return has_en
    except Exception as exc:  # noqa: BLE001
        print(f"   FAIL — {exc}")
        return False


def check_detect(cfg: dict) -> bool:
    _h("3. Detect — POST /detect auto-detects each sample")
    all_ok = True
    for text, label in SAMPLES:
        try:
            resp = requests.post(
                f"{cfg['endpoint']}/detect",
                params={"api-version": API_VERSION},
                headers=_headers(cfg["key"], cfg["region"]),
                json=[{"text": text}],
                timeout=60,
            )
            if not resp.ok:
                _auth_hint(resp, cfg["region"])
                print(f"   {label:9} FAIL — HTTP {resp.status_code}: {resp.text[:200]}")
                all_ok = False
                continue
            item = resp.json()[0]
            print(f"   {label:9} detected = {item.get('language')!r}  score = {item.get('score')}")
        except Exception as exc:  # noqa: BLE001
            print(f"   {label:9} FAIL — {exc}")
            all_ok = False
    print("   PASS" if all_ok else "   FAIL")
    return all_ok


def check_translate(cfg: dict) -> bool:
    _h("4. Translate -> English — POST /translate (auto-detect source, to=en)")
    all_ok = True
    for text, label in SAMPLES:
        try:
            resp = requests.post(
                f"{cfg['endpoint']}/translate",
                params={"api-version": API_VERSION, "to": TARGET},  # no 'from' -> auto-detect
                headers=_headers(cfg["key"], cfg["region"]),
                json=[{"text": text}],
                timeout=120,
            )
            if not resp.ok:
                _auth_hint(resp, cfg["region"])
                print(f"   {label:9} FAIL — HTTP {resp.status_code}: {resp.text[:200]}")
                all_ok = False
                continue
            payload = resp.json()[0]
            detected = (payload.get("detectedLanguage") or {}).get("language", "?")
            english = payload["translations"][0]["text"]
            print(f"   {label:9} [{detected}] -> {english!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"   {label:9} FAIL — {exc}")
            all_ok = False
    print("   PASS" if all_ok else "   FAIL")
    return all_ok


def main() -> int:
    print("Azure AI Translator (Text v3.0) — credential & capability check (language-agnostic)")
    cfg_ok, cfg = check_config()

    results = {"1. Config": cfg_ok}
    if cfg_ok:
        results["2. Connectivity"] = check_connectivity(cfg)
        results["3. Detect"] = check_detect(cfg)
        results["4. Translate->EN"] = check_translate(cfg)
    else:
        print("\nSkipping checks 2-4 — fix the config first.")

    _h("RESULTS")
    for name, ok in results.items():
        print(f"   {'PASS' if ok else 'FAIL'}  {name}")
    all_passed = all(results.values()) and len(results) == 4
    print("\n" + ("ALL CHECKS PASSED — Translator is ready for ingestion." if all_passed
                   else "SOME CHECKS FAILED — see messages above."))
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
