import logging
from typing import Optional


def setup_logging(level: str = "INFO", handler: Optional[logging.Handler] = None) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[handler] if handler else None,
    )
