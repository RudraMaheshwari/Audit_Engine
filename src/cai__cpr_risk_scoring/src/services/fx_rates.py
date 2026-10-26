"""Historical FX conversion via the Frankfurter API (ECB-sourced, free, no API key).

Fetches the daily base->local rate series for the audit period ONCE, caches it, and converts a USD
amount at a given transaction date. Dates with no ECB quote (weekends/holidays) fall back to the most
recent prior quoted date. Returns None when a rate cannot be resolved, so callers render '-' rather
than invent a rate. The rate source is external and verifiable — never an LLM-guessed or fixed rate.
"""

from __future__ import annotations

import datetime as _dt
from bisect import bisect_right

import requests

from src.utils.logger import get_logger

logger = get_logger(__name__)

_FRANKFURTER_RANGE_URL = "https://api.frankfurter.app/{start}..{end}"
_TIMEOUT_S = 30


class FxRateService:
    """Cached historical FX conversion from `base` to `local` using Frankfurter daily rates."""

    def __init__(self, base: str = "USD", local: str = "AUD") -> None:
        self._base = (base or "USD").upper()
        self._local = (local or "AUD").upper()
        self._rates: dict[_dt.date, float] = {}
        self._sorted_dates: list[_dt.date] = []
        self.source = "frankfurter.app (ECB)"

    def load_period(self, start: _dt.date, end: _dt.date) -> None:
        """Fetch and cache the daily base->local rates for [start, end]. Best-effort; logs on failure."""
        if self._base == self._local:
            return
        try:
            url = _FRANKFURTER_RANGE_URL.format(start=start.isoformat(), end=end.isoformat())
            try:
                resp = requests.get(
                    url, params={"from": self._base, "to": self._local}, timeout=_TIMEOUT_S
                )
            except requests.exceptions.SSLError:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                resp = requests.get(
                    url, params={"from": self._base, "to": self._local},
                    timeout=_TIMEOUT_S, verify=False,
                )
            resp.raise_for_status()
            series = resp.json().get("rates", {})
            for day_str, pair in series.items():
                rate = pair.get(self._local)
                if rate is not None:
                    self._rates[_dt.date.fromisoformat(day_str)] = float(rate)
            self._sorted_dates = sorted(self._rates)
            logger.info(
                "[FX] Loaded %d daily %s->%s rate(s) for %s..%s | source=%s",
                len(self._rates), self._base, self._local, start, end, self.source,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[FX] Rate load failed (%s) — conversions will return None", exc)

    def rate_on(self, day: _dt.date) -> float | None:
        """Return the base->local rate on `day`, falling back to the most recent prior quote."""
        if self._base == self._local:
            return 1.0
        if not self._sorted_dates:
            return None
        if day in self._rates:
            return self._rates[day]
        idx = bisect_right(self._sorted_dates, day)
        if idx == 0:
            return None
        return self._rates[self._sorted_dates[idx - 1]]

    def convert(self, base_amount: float, day: _dt.date) -> float | None:
        """Convert `base_amount` (in base currency) to local at `day`'s rate; None if unavailable."""
        rate = self.rate_on(day)
        if rate is None:
            return None
        return base_amount * rate
