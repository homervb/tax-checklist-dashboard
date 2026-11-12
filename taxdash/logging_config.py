"""Basic logging setup for the tax checklist dashboard."""

from __future__ import annotations

import logging
import os
from pathlib import Path

_CONFIGURED = False


def setup_logging() -> logging.Logger:
    """Configure root app logger once; return the taxdash logger."""
    global _CONFIGURED
    logger = logging.getLogger("taxdash")
    if _CONFIGURED:
        return logger

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)

        log_dir = Path(__file__).resolve().parent.parent / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_dir / "taxdash.log")
            file_handler.setFormatter(handler.formatter)
            logger.addHandler(file_handler)
        except OSError:
            # File logging is best-effort for local/dev environments.
            pass

    logger.propagate = False
    _CONFIGURED = True
    return logger
