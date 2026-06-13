from __future__ import annotations

import json
import logging
from typing import Any

__all__ = ("JSONFormatter",)


class JSONFormatter(logging.Formatter):
    """A structured JSON log formatter for file-based logging.

    Each log line is a single JSON object with standard fields, making it
    easy to parse with tools like jq, Loki, or Datadog.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)

        for key in ("guild_id", "user_id", "command", "duration_ms"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)

        return json.dumps(entry, default=str)
