"""Scoping Deck generator entry point.

Runs independently of the main audit pipeline but reuses the same .env / Settings.

Usage (from the project root):
  python -m scoping_deck.main                 ingest (if needed) + generate + render
  python -m scoping_deck.main --force-ingest  re-ingest even if the index is already populated
  python -m scoping_deck.main --skip-ingest   skip ingestion; only generate + render
"""

from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv()

from .src.app import ScopingDeckApp, ScopingDeckResult
from .src.config import ConfigLoader


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scoping Deck generator")
    parser.add_argument("--force-ingest", action="store_true",
                        help="Re-ingest questionnaires even if the index is already populated")
    parser.add_argument("--skip-ingest", action="store_true",
                        help="Skip ingestion; only generate the Market Overview and render the deck")
    return parser.parse_args()


def _print_result(entity_name: str, result: ScopingDeckResult) -> None:
    print("\n" + "=" * 80)
    print(f"MARKET OVERVIEW — {entity_name}")
    print("=" * 80 + "\n")
    print(result.market_overview)
    print("\n" + "=" * 80)
    print(f"Saved Scoping Deck to: {result.deck_path}")


def main() -> None:
    args = _parse_args()
    config = ConfigLoader.load()
    app = ScopingDeckApp(config)
    result = app.run(skip_ingest=args.skip_ingest, force_ingest=args.force_ingest)
    _print_result(config.entity_name, result)


if __name__ == "__main__":
    main()
