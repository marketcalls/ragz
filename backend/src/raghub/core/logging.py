import logging
import re
from collections.abc import MutableMapping
from typing import Any

import structlog

_SENSITIVE = re.compile(r"password|secret|token|api_key|(?:^|_)key$", re.IGNORECASE)


def redact_sensitive(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for k in list(event_dict):
        if _SENSITIVE.search(k):
            event_dict[k] = "[REDACTED]"
    return event_dict


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_sensitive,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )
