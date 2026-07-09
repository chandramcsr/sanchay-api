"""
Structured logging setup. Replaces ad-hoc logging.getLogger(...) calls
scattered with no consistent format — this gives every log line the
same shape (timestamp, level, logger name, message), which is what
makes Render's log viewer (or any future log aggregator) actually
searchable during a real incident instead of a wall of inconsistent
free text.

Deliberately NOT full JSON structured logging (e.g. via structlog) —
that's a real upgrade worth making the day there's an actual log
aggregation/search tool consuming these logs (Datadog, Grafana Loki,
etc.), which doesn't exist yet at this scale. Plain formatted text is
the right amount of investment for "readable in Render's raw log
tail," not more.
"""

import logging
import sys


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    # Quiet a couple of noisy third-party loggers down to warnings —
    # every successful health-check ping doesn't need its own line.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
