"""Scoping Deck job — generates the CPR Scoping Deck (Scoping_Deck engine).

ScopingDeckApp.run() generates all slide content concurrently internally, so the service emits the
first (Market Overview) and last (Assemble Deck) SD_* phases around the single run. The run is
synchronous, so it executes in a thread. Finer SD_* emission is wired by passing the reporter into the
deck app's generators.
"""

from __future__ import annotations

import asyncio

from app.schemas.runs import StartRunRequest
from app.services.progress import ProgressReporter


async def run(reporter: ProgressReporter, request: StartRunRequest) -> None:
    from Scoping_Deck.src.app import ScopingDeckApp
    from Scoping_Deck.src.config import ConfigLoader

    reporter.phase("SD_MARKET_OVERVIEW")
    config = await asyncio.to_thread(ConfigLoader.load)

    def _generate():
        return ScopingDeckApp(config).run(
            skip_ingest=False, force_ingest=request.force_ingest, on_phase=reporter.phase,
        )

    result = await asyncio.to_thread(_generate)

    reporter.phase("SD_ASSEMBLE_DECK")

    deck_path = getattr(result, "deck_path", "")
    volume_path = None
    if deck_path:
        from src.pipeline.output_upload import upload_output_file
        # Deck is OVERWRITTEN each run with its own name (latest only, for download).
        volume_path = await asyncio.to_thread(
            upload_output_file, config.settings, deck_path,
            dest_volume=config.settings.scoping_deck_output_volume_path, keep_name=True,
        )
    # Scoping Deck produces only the .pptx (no Delta tables); db_tables kept for a uniform result shape.
    reporter.succeed({
        "deck_path": str(deck_path),
        "volume_path": volume_path,
        "db_tables": {},
    })
