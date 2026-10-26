import logging
import sys
from typing import Any


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)-40s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def log_extraction_summary(
    logger: logging.Logger,
    file_name: str,
    file_type: str,
    char_count: int,
    extra: dict[str, Any] | None = None,
) -> None:
    """Standardised extraction log — called by every extractor after extraction."""
    msg = (
        f"[EXTRACT] {file_type.upper():8s} | file={file_name} | "
        f"chars={char_count:,}"
    )
    if extra:
        for k, v in extra.items():
            msg += f" | {k}={v}"
    logger.info(msg)


def log_chunk_summary(
    logger: logging.Logger,
    file_name: str,
    total_chunks: int,
    avg_tokens: float,
    sections_detected: int,
    chunk_size: int,
    overlap: int,
) -> None:
    """Standardised chunking log — called by Chunker after each document is chunked."""
    logger.info(
        "[CHUNK]   file=%-40s | chunks=%3d | avg_tokens=%5.0f | "
        "sections_detected=%2d | chunk_size=%d | overlap=%d",
        file_name, total_chunks, avg_tokens, sections_detected, chunk_size, overlap,
    )
