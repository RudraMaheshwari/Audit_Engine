"""Language-agnostic translation-to-English for ingestion.

Any NON-ENGLISH text that enters the system — supporting docs, SI / Legal / Additional Input /
past-audit knowledge, questionnaire Q&A, and TPI inventory names — is auto-detected and translated to
English via Azure OpenAI, so retrieval, reasoning, scope/keyword matching and the auditor-facing
output are all in one language.

Design:
  * Language detection is script-based (CJK / Hangul / Arabic / Cyrillic / … ranges). English and
    other Latin-script text are passed through UNTOUCHED — no cost, no latency for the existing
    auditors.
  * Best-effort: any failure falls back to the ORIGINAL text so an ingestion run never breaks.
  * Batch path for many short strings (names, Q&A cells) — one LLM call per ~40 unique snippets — plus
    a chunked path for long documents.
"""

from __future__ import annotations

import json
import re
import threading
import time

from src.config import constants
from src.config.settings import Settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Non-Latin script ranges: Greek, Cyrillic, Hebrew, Arabic, Devanagari, Thai, Hiragana/Katakana, CJK
# ideographs, Hangul, CJK-compat, fullwidth forms. Accented Latin (é, ñ, ü) is intentionally EXCLUDED,
# so ordinary Latin-script text is treated as English-ish and left alone (no needless translation).
_NON_LATIN = re.compile(
    r"[Ͱ-ϿЀ-ӿ֐-׿؀-ۿऀ-ॿ฀-๿"
    r"぀-ヿ㐀-䶿一-鿿ꀀ-꓏가-힯豈-﫿＀-￯]"
)

# Characteristic-script -> language, checked IN ORDER. Kana (unique to Japanese) is checked before Han,
# so Japanese text — which also contains Han ideographs — is identified as Japanese, not Chinese.
_SCRIPT_LANGUAGES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"[぀-ゟ゠-ヿ]"), "Japanese"),        # Hiragana / Katakana
    (re.compile(r"[가-힣ᄀ-ᇿ]"), "Korean"),          # Hangul
    (re.compile(r"[؀-ۿݐ-ݿ]"), "Arabic"),
    (re.compile(r"[฀-๿]"), "Thai"),
    (re.compile(r"[ऀ-ॿ]"), "Hindi"),
    (re.compile(r"[֐-׿]"), "Hebrew"),
    (re.compile(r"[Ѐ-ӿ]"), "Russian"),
    (re.compile(r"[Ͱ-Ͽ]"), "Greek"),
    (re.compile(r"[一-鿿㐀-䶿]"), "Chinese"),          # Han (after the kana check)
]

# Latin-script FOREIGN languages (French, Spanish, German, …) share the English alphabet, so the
# script ranges above can't catch them. For substantial Latin-script text we fall back to langdetect
# (optional dependency) to identify the ISO code and translate anything that isn't English.
_ISO_TO_NAME: dict[str, str] = {
    "fr": "French", "es": "Spanish", "de": "German", "it": "Italian", "pt": "Portuguese",
    "nl": "Dutch", "pl": "Polish", "tr": "Turkish", "sv": "Swedish", "da": "Danish", "no": "Norwegian",
    "fi": "Finnish", "cs": "Czech", "hu": "Hungarian", "ro": "Romanian", "ca": "Catalan",
    "ja": "Japanese", "ko": "Korean", "zh-cn": "Chinese", "zh-tw": "Chinese", "zh": "Chinese",
    "ar": "Arabic", "ru": "Russian", "uk": "Ukrainian", "el": "Greek", "he": "Hebrew", "hi": "Hindi",
    "th": "Thai", "vi": "Vietnamese", "id": "Indonesian",
}
_LANGDETECT_MIN_CHARS = constants.LANGDETECT_MIN_CHARS
_LANGDETECT_SAMPLE_CHARS = constants.LANGDETECT_SAMPLE_CHARS


def _detect_iso(text: str) -> str | None:
    """Best-effort ISO language code via langdetect (deterministic seed). None if the lib is absent
    or detection fails — callers then fall back to the non-Latin-script heuristic."""
    try:
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 0
        return detect(text[:_LANGDETECT_SAMPLE_CHARS])
    except Exception:  # noqa: BLE001 — langdetect not installed / undetermined
        return None


_MIN_NONLATIN_RATIO = constants.TRANSLATE_MIN_NONLATIN_RATIO
_SHORT_LEN = constants.TRANSLATE_SHORT_LEN
_BATCH_SHORT_LEN = constants.TRANSLATE_BATCH_SHORT_LEN
_DOC_CHUNK_CHARS = constants.TRANSLATE_DOC_CHUNK_CHARS
_BATCH_SIZE = constants.TRANSLATE_BATCH_SIZE
_TRANSLATE_WORKERS = constants.TRANSLATE_WORKERS
_PROGRESS_EVERY = constants.TRANSLATE_PROGRESS_EVERY

# ── Azure AI Translator (Text v3.0) — optional dedicated NMT engine (translation_engine) ──────────
_AZURE_GLOBAL_ENDPOINT = constants.AZURE_TRANSLATOR_GLOBAL_ENDPOINT
_AZURE_BATCH_CHARS = constants.AZURE_TRANSLATOR_BATCH_CHARS
_AZURE_BATCH_ITEMS = constants.AZURE_TRANSLATOR_BATCH_ITEMS
_AZURE_PROGRESS_EVERY = constants.AZURE_TRANSLATOR_PROGRESS_EVERY
_AZURE_MAX_RETRIES = constants.AZURE_TRANSLATOR_MAX_RETRIES
_AZURE_RETRY_BACKOFF = constants.AZURE_TRANSLATOR_RETRY_BACKOFF
_AZURE_THROTTLE_RETRIES = constants.AZURE_TRANSLATOR_THROTTLE_RETRIES
_AZURE_THROTTLE_MAX_WAIT = constants.AZURE_TRANSLATOR_THROTTLE_MAX_WAIT
_AZURE_MAX_CONCURRENCY = constants.AZURE_TRANSLATOR_MAX_CONCURRENCY
_AZURE_MIN_INTERVAL = constants.AZURE_TRANSLATOR_MIN_INTERVAL
_AZURE_CHARS_PER_MINUTE = constants.AZURE_TRANSLATOR_CHARS_PER_MINUTE
_AZURE_BUCKET_BURST_SECONDS = constants.AZURE_TRANSLATOR_BUCKET_BURST_SECONDS
_AZURE_CONNECT_TIMEOUT = constants.AZURE_TRANSLATOR_CONNECT_TIMEOUT
_AZURE_READ_TIMEOUT = constants.AZURE_TRANSLATOR_READ_TIMEOUT
_AZURE_API_VERSION = constants.AZURE_TRANSLATOR_API_VERSION
_AZURE_ENDPOINT_PROBE_TIMEOUT = constants.AZURE_TRANSLATOR_ENDPOINT_PROBE_TIMEOUT
_BUCKET_WAIT_MIN_S = constants.AZURE_TRANSLATOR_BUCKET_WAIT_MIN_S
_BUCKET_WAIT_MAX_S = constants.AZURE_TRANSLATOR_BUCKET_WAIT_MAX_S

# Language NAME -> Azure ISO code, for translating INTO a local language (bilingual Output A).
_NAME_TO_ISO: dict[str, str] = {
    "Japanese": "ja", "Korean": "ko", "Chinese": "zh-Hans", "Arabic": "ar", "French": "fr",
    "Spanish": "es", "German": "de", "Italian": "it", "Portuguese": "pt", "Dutch": "nl",
    "Russian": "ru", "Thai": "th", "Hindi": "hi", "Hebrew": "he", "Greek": "el", "Vietnamese": "vi",
    "Indonesian": "id", "Polish": "pl", "Turkish": "tr", "Swedish": "sv", "Danish": "da",
    "Norwegian": "no", "Finnish": "fi", "Czech": "cs", "Hungarian": "hu", "Romanian": "ro",
    "Ukrainian": "uk", "Catalan": "ca",
}


def _text_base(endpoint: str) -> str:
    """Resolve the Text-Translation base URL. A custom subdomain resource serves it under
    /translator/text/v3.0; the global host serves /translate at the root."""
    e = (endpoint or "").strip().rstrip("/")
    host = e.split("://", 1)[-1].split("/", 1)[0].lower()
    if host.endswith("cognitiveservices.azure.com") and "/translator/text" not in e:
        return e + "/translator/text/v3.0"
    return e


_truststore_lock = threading.Lock()
_truststore_ready = False


def _ensure_os_truststore() -> None:
    """Make Python validate TLS against the OPERATING SYSTEM trust store (via `truststore`), so a
    corporate proxy's root CA — which otherwise breaks certifi-based verification with an SSLError on
    the translator host — is trusted. Injected once per process, best-effort: if `truststore` is
    missing or injection fails, the default certifi TLS is kept unchanged."""
    global _truststore_ready
    if _truststore_ready:
        return
    with _truststore_lock:
        if _truststore_ready:
            return
        try:
            import truststore

            truststore.inject_into_ssl()
            logger.info("[Translate] TLS now verifies against the OS trust store (truststore injected)")
        except Exception as exc:  # noqa: BLE001 — package absent / inject failed: keep default TLS
            logger.warning("[Translate] Could not enable OS trust store (%s) — using default TLS", exc)
        _truststore_ready = True


_session_lock = threading.Lock()
_session = None


def _azure_session():
    """A shared, pooled `requests.Session` for all Azure Translator calls. Connection keep-alive means
    we stop opening a brand-new TLS connection per batch — the main cause of UNEXPECTED_EOF / connection
    resets through a corporate proxy. Transport-level retries are DISABLED here (max_retries=0): the
    application loop in `_azure_post_batch` owns all retry logic so that 429 throttling is handled once,
    honouring Retry-After, with a single clear budget. Built once, thread-safe."""
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is not None:
            return _session
        import requests
        from requests.adapters import HTTPAdapter

        adapter = HTTPAdapter(max_retries=0, pool_connections=10, pool_maxsize=10)
        s = requests.Session()
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _session = s
        return _session


_gate_lock = threading.Lock()
_azure_gate_sem = None


def _azure_gate(settings):
    """A process-wide semaphore that caps how many Azure Translator requests run AT ONCE across every
    file-worker. Parallel bursts from 4 workers translating big ledgers simultaneously are what trip the
    tier's chars-per-minute quota (HTTP 429); serialising to a small number keeps us under it. Size from
    `translation_max_concurrency` (default `_AZURE_MAX_CONCURRENCY`). Built once, thread-safe."""
    global _azure_gate_sem
    if _azure_gate_sem is not None:
        return _azure_gate_sem
    with _gate_lock:
        if _azure_gate_sem is None:
            n = max(1, int(getattr(settings, "translation_max_concurrency", _AZURE_MAX_CONCURRENCY)))
            _azure_gate_sem = threading.BoundedSemaphore(n)
            logger.info("[Translate] Azure Translator request concurrency capped at %d (across all workers)", n)
    return _azure_gate_sem


_bucket_lock = threading.Lock()
_bucket_tokens = None   # float; None = uninitialised
_bucket_last = 0.0      # monotonic timestamp of last refill


def _azure_rate_limit(settings, n_chars: int) -> None:
    """Token-bucket limiter that caps the SUSTAINED character rate (chars/minute) across all workers, so
    we stay just under the resource's sliding-window throttle and PROACTIVELY avoid 429s — rather than
    flooring it and recovering with backoff. Refills at `chars_per_minute/60` per second; a request waits
    until its character count is affordable. Budget from `translation_chars_per_minute`; 0 disables it."""
    cpm = float(getattr(settings, "translation_chars_per_minute", _AZURE_CHARS_PER_MINUTE))
    if cpm <= 0:
        return
    rate = cpm / 60.0            # chars per second
    # Small burst allowance only (a couple of seconds, but at least one max-size request) — NOT a whole
    # minute, otherwise the bucket would let ~1 min of characters flood out at once and trip the very
    # sliding-window throttle we're avoiding.
    capacity = max(float(_AZURE_BATCH_CHARS), rate * _AZURE_BUCKET_BURST_SECONDS)
    need = min(float(n_chars), capacity)
    global _bucket_tokens, _bucket_last
    while True:
        with _bucket_lock:
            now = time.monotonic()
            if _bucket_tokens is None:
                _bucket_tokens, _bucket_last = capacity, now
            _bucket_tokens = min(capacity, _bucket_tokens + (now - _bucket_last) * rate)
            _bucket_last = now
            if _bucket_tokens >= need:
                _bucket_tokens -= need
                return
            wait = (need - _bucket_tokens) / rate
        time.sleep(min(max(wait, _BUCKET_WAIT_MIN_S), _BUCKET_WAIT_MAX_S))   # re-loop to refill (lock released during the wait)


_pace_lock = threading.Lock()
_pace_next_slot = 0.0


def _azure_pace(settings) -> None:
    """Enforce a minimum GAP between the START of consecutive Azure Translator requests, process-wide, so
    the character rate stays under the tier's per-minute throttle (anti-429). Slots are reserved under a
    lock (atomic, fair across threads) and the actual wait happens outside the lock. Interval from
    `translation_min_request_interval`; <= 0 disables pacing."""
    global _pace_next_slot
    interval = float(getattr(settings, "translation_min_request_interval", _AZURE_MIN_INTERVAL))
    if interval <= 0:
        return
    with _pace_lock:
        now = time.monotonic()
        start = max(now, _pace_next_slot)
        _pace_next_slot = start + interval
        wait = start - now
    if wait > 0:
        time.sleep(wait)


def _retry_after_seconds(resp) -> float | None:
    """Seconds to wait from a 429/503 'Retry-After' header (delta-seconds form). None if absent/unparsable."""
    raw = (resp.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None

_SYSTEM_PROMPT = (
    "You are a professional translator for a corporate compliance audit. Translate the user's text "
    "into clear, natural English. Rules: (1) auto-detect the source language; (2) translate EVERYTHING, "
    "never summarise or omit; (3) preserve the structure — keep line breaks and keep table rows written "
    "with ' | ' separators as-is; (4) keep numbers, dates, monetary amounts and proper nouns (people, "
    "companies, systems) accurate; (5) output ONLY the English translation — no notes, no preamble. If "
    "the text is already English, return it unchanged."
)

_BATCH_SYSTEM_PROMPT = (
    "You translate short text snippets to English for a compliance audit. You receive a JSON array of "
    "strings. Return ONLY a JSON array of the SAME length and order, each element the English "
    "translation of the corresponding input (already-English strings returned unchanged). Keep proper "
    "nouns and company names accurate. Output JSON only — no prose, no code fences."
)


def _strip_code_fence(raw: str) -> str:
    """Strip a ```json … ``` fence an LLM sometimes wraps its JSON reply in; returns the inner text."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.lower().startswith("json"):
            raw = raw[len("json"):]
        raw = raw.strip()
    return raw


def _chunk(text: str, max_chars: int) -> list[str]:
    """Split text into <= max_chars chunks on paragraph boundaries."""
    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        # Index-based slicing (not repeated `para = para[max_chars:]`, which is O(n²) and makes a
        # single huge single-sheet ledger take minutes just to chunk).
        start = 0
        while len(para) - start > max_chars:
            chunks.append(para[start:start + max_chars])
            start += max_chars
        current = para[start:]
    if current:
        chunks.append(current)
    return chunks


class TranslationService:
    """Detect non-English text and translate it to English via Azure OpenAI. Best-effort throughout."""

    def __init__(self, settings: Settings, llm=None) -> None:
        self._settings = settings
        self._llm = llm
        self._enabled = bool(getattr(settings, "translate_to_english", True))
        self._azure_base_cached = None     # None = unresolved; "" = resolved-to-none; else base URL
        self._azure_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _client(self):
        if self._llm is None:
            from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider

            self._llm = AzureOpenAILLMProvider(self._settings).get_client()
        return self._llm

    # ── Azure AI Translator engine ────────────────────────────────────────────────────────────────
    def _use_azure(self) -> bool:
        return (str(getattr(self._settings, "translation_engine", "openai")).lower() == "azure_translator"
                and bool(getattr(self._settings, "azure_translator_key", "")))

    def _azure_headers(self) -> dict:
        h = {
            "Ocp-Apim-Subscription-Key": self._settings.azure_translator_key,
            "Content-Type": "application/json",
        }
        region = getattr(self._settings, "azure_translator_region", "") or ""
        if region:
            h["Ocp-Apim-Subscription-Region"] = region
        return h

    def _azure_base(self):
        """Resolve (once, cached) the first reachable Text-Translation base URL: the configured custom
        host, then the global host. Returns the base URL or None if neither is reachable."""
        if self._azure_base_cached is not None:
            return self._azure_base_cached or None
        _ensure_os_truststore()   # trust the corporate root CA before the first TLS call (SSL fix)
        with self._azure_lock:
            if self._azure_base_cached is not None:
                return self._azure_base_cached or None
            session = _azure_session()
            for ep in (getattr(self._settings, "azure_translator_endpoint", ""), _AZURE_GLOBAL_ENDPOINT):
                if not ep:
                    continue
                base = _text_base(ep)
                try:
                    r = session.post(f"{base}/translate",
                                     params={"api-version": _AZURE_API_VERSION, "to": "en"},
                                     headers=self._azure_headers(), json=[{"text": "テスト"}],
                                     timeout=_AZURE_ENDPOINT_PROBE_TIMEOUT)
                    if r.ok:
                        logger.info("[Translate] Azure Translator endpoint selected | %s", base)
                        self._azure_base_cached = base
                        return base
                    logger.warning("[Translate] Azure endpoint %s -> HTTP %s; trying next", base, r.status_code)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[Translate] Azure endpoint %s unreachable (%s); trying next", base, exc)
            logger.warning("[Translate] No reachable Azure Translator endpoint — keeping original text (no fallback)")
            self._azure_base_cached = ""
            return None

    def _azure_post_batch(self, url: str, params: dict, headers: dict, batch: list[str],
                          tag: str, batch_no: int, chars: int) -> list[str] | None:
        """POST one batch to Azure Translator, returning the translations or None when it truly can't be
        translated. Two independent budgets, so a slow quota window never costs data:
          * 429 (Too Many Requests): waited out up to `_AZURE_THROTTLE_RETRIES` times, honouring the
            server's Retry-After header — throttling pauses us, it does NOT drop the batch.
          * SSL / connection / read-timeout / 5xx: retried up to `_AZURE_MAX_RETRIES` with backoff.
        Every failure is logged with detail (status / SSL, attempt, item & char counts). The request is
        gated by the process-wide concurrency semaphore so parallel workers don't re-trigger throttling."""
        import requests

        session = _azure_session()
        gate = _azure_gate(self._settings)
        label = tag or "batch"
        payload = [{"text": t} for t in batch]
        net_fails = 0        # SSL / connection / timeout / 5xx
        throttles = 0        # 429
        while True:
            try:
                _azure_rate_limit(self._settings, chars)   # PRIMARY: stay under the chars/min throttle
                _azure_pace(self._settings)                # secondary: min gap between consecutive requests
                with gate:                    # cap simultaneous requests across all workers (anti-429)
                    resp = session.post(url, params=params, headers=headers, json=payload,
                                        timeout=(_AZURE_CONNECT_TIMEOUT, _AZURE_READ_TIMEOUT))
            except requests.exceptions.SSLError as exc:
                net_fails += 1
                logger.warning("[Translate] %s | Azure SSL error | batch=%d attempt=%d/%d items=%d chars=%d | %s",
                               label, batch_no, net_fails, _AZURE_MAX_RETRIES, len(batch), chars, exc)
                if net_fails >= _AZURE_MAX_RETRIES:
                    break
                time.sleep(_AZURE_RETRY_BACKOFF * net_fails); continue
            except requests.exceptions.RequestException as exc:   # ConnectionError, ReadTimeout, …
                net_fails += 1
                logger.warning("[Translate] %s | Azure request error | batch=%d attempt=%d/%d items=%d | %s",
                               label, batch_no, net_fails, _AZURE_MAX_RETRIES, len(batch), exc)
                if net_fails >= _AZURE_MAX_RETRIES:
                    break
                time.sleep(_AZURE_RETRY_BACKOFF * net_fails); continue

            # 429 throttling — wait it out (Retry-After), separate budget; does not consume net_fails.
            if resp.status_code == 429:
                throttles += 1
                if throttles > _AZURE_THROTTLE_RETRIES:
                    logger.error("[Translate] %s | Azure batch %d still throttled (429) after %d waits — keeping original (%d chunk(s))",
                                 label, batch_no, _AZURE_THROTTLE_RETRIES, len(batch))
                    break
                wait = _retry_after_seconds(resp)
                if wait is None:
                    wait = min(_AZURE_RETRY_BACKOFF * throttles, _AZURE_THROTTLE_MAX_WAIT)
                logger.warning("[Translate] %s | Azure 429 throttled | batch=%d waiting %.0fs (throttle %d/%d) items=%d",
                               label, batch_no, wait, throttles, _AZURE_THROTTLE_RETRIES, len(batch))
                time.sleep(wait); continue

            try:
                resp.raise_for_status()
                return [item["translations"][0]["text"] for item in resp.json()]
            except requests.exceptions.RequestException as exc:   # 5xx and other HTTP errors
                net_fails += 1
                status = getattr(getattr(exc, "response", None), "status_code", "")
                logger.warning("[Translate] %s | Azure HTTP error | batch=%d attempt=%d/%d HTTP=%s items=%d | %s",
                               label, batch_no, net_fails, _AZURE_MAX_RETRIES, status, len(batch), exc)
                if net_fails >= _AZURE_MAX_RETRIES:
                    break
                time.sleep(_AZURE_RETRY_BACKOFF * net_fails); continue
            except Exception as exc:  # noqa: BLE001 — malformed body / unexpected shape
                logger.warning("[Translate] %s | Azure response parse error | batch=%d | %s", label, batch_no, exc)
                break
        return None

    def _azure_translate(self, texts: list[str], to: str, tag: str = "") -> list[str] | None:
        """Translate `texts` to `to` via Azure Translator, batched (≤900 items / ≤45k chars per call).
        Returns translations in input order. None ONLY when no Azure endpoint is reachable — the caller
        then keeps the ORIGINAL text; there is NO OpenAI fallback (we rely solely on the translator key).
        A batch that keeps failing (e.g. an SSL error) is retried `_AZURE_MAX_RETRIES` times and then
        keeps its ORIGINAL text, so a transient fault never aborts the document or loses data. The
        sequential loop logs cumulative progress every `_AZURE_PROGRESS_EVERY` batches when a `tag` is
        given, so a large document is not a silent black box between ▶ and ✔."""
        base = self._azure_base()
        if not base:
            return None
        url = f"{base}/translate"
        params = {"api-version": _AZURE_API_VERSION, "to": to}   # no 'from' -> auto-detect
        headers = self._azure_headers()
        results: list[str] = []
        total = len(texts)
        i = 0
        batch_no = 0
        failed_batches = 0
        while i < len(texts):
            batch, chars = [], 0
            while i < len(texts) and len(batch) < _AZURE_BATCH_ITEMS and (not batch or chars + len(texts[i]) <= _AZURE_BATCH_CHARS):
                batch.append(texts[i][:_AZURE_BATCH_CHARS]); chars += len(texts[i]); i += 1
            batch_no += 1
            out = self._azure_post_batch(url, params, headers, batch, tag, batch_no, chars)
            if out is None:                    # retries exhausted — keep originals (no data loss)
                failed_batches += 1
                out = list(batch)
            results.extend(out)
            if tag and (len(results) >= total or batch_no % _AZURE_PROGRESS_EVERY == 0):
                logger.info("[Translate] … %s | Azure Translator | %d/%d chunks | %d batches | %d failed",
                            tag, len(results), total, batch_no, failed_batches)
        if failed_batches:
            logger.warning("[Translate] %s | Azure Translator | %d/%d batch(es) kept ORIGINAL text after retries",
                           tag or "batch", failed_batches, batch_no)
        return results if len(results) == len(texts) else None

    @staticmethod
    def detect_language(text: str | None) -> str:
        """Identify the dominant NON-ENGLISH language NAME in `text` (e.g. 'Japanese'), or '' when the
        text looks English / undetermined. Script-based and dependency-free: the kana check comes before
        the Han check so Japanese (which also uses Han) is not misread as Chinese."""
        if not TranslationService.needs_translation(text):
            return ""
        for pattern, language in _SCRIPT_LANGUAGES:
            if pattern.search(text):
                return language
        # Latin-script foreign language — resolve the name from the langdetect ISO code.
        if text and len(text) >= _LANGDETECT_MIN_CHARS:
            code = _detect_iso(text)
            if code and code.split("-")[0] != "en":
                return _ISO_TO_NAME.get(code, _ISO_TO_NAME.get(code.split("-")[0], ""))
        return ""

    @staticmethod
    def needs_translation(text: str | None) -> bool:
        """True when `text` is non-English and worth translating.
        Fast path: non-Latin script (CJK / Hangul / Arabic / … — also catches short names/cells).
        Latin-script path: substantial text whose langdetect code is not English (French, Spanish, …)."""
        if not text:
            return False
        nonlatin = len(_NON_LATIN.findall(text))
        if nonlatin:
            if len(text) <= _SHORT_LEN:      # names / short cells — any non-Latin char counts
                return True
            if nonlatin / max(len(text), 1) >= _MIN_NONLATIN_RATIO:
                return True
        # Latin-script foreign language (French, Spanish, German, …): detect on substantial text only.
        if len(text) >= _LANGDETECT_MIN_CHARS:
            code = _detect_iso(text)
            if code and code.split("-")[0] != "en":
                return True
        return False

    def translate_text(self, text: str | None, label: str = "") -> str:
        """Translate one (possibly long) text to English. Returns the original when it is already
        English, translation is disabled, or the call fails. `label` (e.g. a file name) is included in
        the progress logs so you can see which document is being translated and how far it is.

        Uses the configured engine (Azure Translator when selected + reachable, else Azure OpenAI). The
        OpenAI path splits a long document into chunks translated CONCURRENTLY (order preserved). Nothing
        is ever skipped: a failed chunk keeps its original text (no data loss)."""
        if not self._enabled or not text or not self.needs_translation(text):
            return text or ""
        tag = label or "document"
        if self._use_azure():
            result = self._azure_translate_text(text, tag)
            if result is not None:
                return result
            # No OpenAI fallback — rely solely on the Azure Translator key. Keep original text (no loss).
            logger.warning("[Translate] %s | No reachable Azure Translator endpoint — keeping original text", tag)
            return text
        return self._openai_translate_text(text, tag)

    def _azure_translate_text(self, text: str, tag: str) -> str | None:
        chunks = _chunk(text, _DOC_CHUNK_CHARS)
        logger.info("[Translate] ▶ %s | Azure Translator | %d chunk(s) | %d chars", tag, len(chunks), len(text))
        translated = self._azure_translate(chunks, "en", tag=tag)
        if translated is None:
            return None
        logger.info("[Translate] ✔ %s | Azure Translator | %d chunk(s) translated", tag, len(chunks))
        return "\n\n".join(translated).strip() or text

    def _openai_translate_text(self, text: str, tag: str) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        chunks = _chunk(text, _DOC_CHUNK_CHARS)

        def _translate_chunk(chunk: str) -> str:
            try:
                resp = self._client().invoke(
                    [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=chunk)]
                )
                return (resp.content or "").strip() or chunk
            except Exception as exc:  # noqa: BLE001 — keep this chunk's original text (no loss)
                logger.warning("[Translate] %s — chunk failed, keeping original chunk | %s", tag, exc)
                return chunk

        if len(chunks) == 1:
            logger.info("[Translate] ▶ %s | translating (1 chunk, %d chars)", tag, len(text))
            result = _translate_chunk(chunks[0]).strip() or text
            logger.info("[Translate] ✔ %s | done", tag)
            return result

        from concurrent.futures import ThreadPoolExecutor, as_completed

        total = len(chunks)
        workers = max(1, int(getattr(self._settings, "translation_workers", _TRANSLATE_WORKERS)))
        eff = min(workers, total)
        logger.info("[Translate] ▶ %s | translating %d chunks | workers=%d | %d chars",
                    tag, total, eff, len(text))
        parts: list[str] = [""] * total
        done = 0
        with ThreadPoolExecutor(max_workers=eff) as pool:
            fut_to_idx = {pool.submit(_translate_chunk, c): i for i, c in enumerate(chunks)}
            for fut in as_completed(fut_to_idx):
                parts[fut_to_idx[fut]] = fut.result()   # index assignment preserves original order
                done += 1
                if done == total or done % _PROGRESS_EVERY == 0:
                    logger.info("[Translate] … %s | %d/%d chunks done", tag, done, total)
        logger.info("[Translate] ✔ %s | %d/%d chunks translated", tag, total, total)
        result = "\n\n".join(p for p in parts if p).strip()
        return result or text

    def translate_batch(self, texts: list[str]) -> list[str]:
        """Translate a list of strings, index-aligned with the input. Only strings that need it are
        sent (deduped); short ones go through a JSON batch call, long ones through translate_text.
        Already-English / empty strings pass through. Best-effort — originals on failure."""
        if not self._enabled or not texts:
            return list(texts)
        uniques = {t for t in texts if t and self.needs_translation(t)}
        if not uniques:
            return list(texts)
        mapping: dict[str, str] = {}
        if self._use_azure():
            # Azure Translator engine — one batched pass over all uniques. NO OpenAI fallback: rely
            # solely on the translator key. Unmapped uniques (no endpoint) keep their original text
            # via mapping.get() below — best-effort, no data loss.
            uq = list(uniques)
            tr = self._azure_translate(uq, "en")
            if tr is not None:
                mapping = {src: out for src, out in zip(uq, tr)}
        else:   # OpenAI engine (only when Azure Translator is not configured)
            short = sorted(u for u in uniques if len(u) <= _BATCH_SHORT_LEN)
            long = [u for u in uniques if len(u) > _BATCH_SHORT_LEN]
            mapping.update(self._translate_short(short))
            for u in long:
                mapping[u] = self.translate_text(u)
        return [mapping.get(t, t) for t in texts]

    def translate_fields_to_language(self, fields: dict[str, str], language: str) -> dict[str, str]:
        """Translate the VALUES of `fields` into `language` (e.g. English findings -> Japanese) in ONE
        keyed LLM call, so a finding's description+rationale are translated together and split reliably.
        Returns {key: translated} for the non-empty inputs. Best-effort — {} on failure/disabled."""
        if not self._enabled or not language:
            return {}
        non_empty = {k: v for k, v in fields.items() if v and str(v).strip()}
        if not non_empty:
            return {}
        # Azure Translator engine — translate each value INTO the local language (by ISO code).
        # NO OpenAI/LLM fallback: rely solely on the Azure Translator key.
        if self._use_azure():
            iso = _NAME_TO_ISO.get(language) or _NAME_TO_ISO.get(language.split()[0] if language else "")
            if not iso:
                logger.warning("[Translate] No Azure ISO code for language '%s' — skipping field translation", language)
                return {}
            keys = list(non_empty)
            tr = self._azure_translate([non_empty[k] for k in keys], iso)
            if tr is not None:
                return {k: tr[i] for i, k in enumerate(keys)}
            logger.warning("[Translate] Azure field translation to %s unavailable — skipping (no LLM fallback)", language)
            return {}
        from langchain_core.messages import HumanMessage, SystemMessage

        system = (
            f"You are a professional translator for a corporate compliance audit. Translate each VALUE "
            f"in the given JSON object into {language}. Return ONLY a JSON object with the SAME keys, "
            f"each value replaced by its {language} translation. Keep numbers, dates, monetary amounts "
            f"and proper nouns accurate; preserve line breaks and any ' | ' table separators. Output "
            f"JSON only — no prose, no code fences."
        )
        try:
            resp = self._client().invoke([
                SystemMessage(content=system),
                HumanMessage(content=json.dumps(non_empty, ensure_ascii=False)),
            ])
            obj = json.loads(_strip_code_fence(resp.content or ""))
            if isinstance(obj, dict):
                return {k: str(obj.get(k, "") or "").strip() for k in non_empty}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Translate] field translation to %s failed | %s", language, exc)
        return {}

    def augment_with_english(self, names: list[str]) -> list[str]:
        """Return `names` plus an English variant for every non-English entry — ORIGINALS KEPT, deduped.

        For entity-name lists (TPI / distributor / PSP / tender) matched against a transactional ledger
        whose names may be in EITHER the original language or romanised English: keeping both variants
        makes the match succeed regardless. Best-effort — returns the originals unchanged on failure."""
        if not self._enabled or not names:
            return list(names)
        try:
            translated = self.translate_batch(names)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Translate] name augmentation failed — keeping originals | %s", exc)
            return list(names)
        out: list[str] = []
        seen: set[str] = set()
        for original, english in zip(names, translated):
            for variant in (original, english):
                v = (variant or "").strip()
                if v and v.lower() not in seen:
                    seen.add(v.lower())
                    out.append(v)
        return out

    def _translate_short(self, uniques: list[str]) -> dict[str, str]:
        from langchain_core.messages import HumanMessage, SystemMessage

        result: dict[str, str] = {}
        for i in range(0, len(uniques), _BATCH_SIZE):
            batch = uniques[i:i + _BATCH_SIZE]
            try:
                resp = self._client().invoke([
                    SystemMessage(content=_BATCH_SYSTEM_PROMPT),
                    HumanMessage(content=json.dumps(batch, ensure_ascii=False)),
                ])
                arr = json.loads(_strip_code_fence(resp.content or ""))
                if isinstance(arr, list) and len(arr) == len(batch):
                    for src, tr in zip(batch, arr):
                        result[src] = (str(tr).strip() or src)
                else:
                    for src in batch:
                        result[src] = src
                    logger.warning("[Translate] batch length mismatch — keeping originals")
            except Exception as exc:  # noqa: BLE001
                logger.warning("[Translate] batch translation failed — keeping originals | %s", exc)
                for src in batch:
                    result[src] = src
        return result
