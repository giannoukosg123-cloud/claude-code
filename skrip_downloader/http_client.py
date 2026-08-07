"""Thin HTTP wrapper: one requests.Session per issue, polite delays,
bounded retries with exponential backoff, generous timeouts.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Optional

import requests

from . import config


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})
    return session


def polite_delay() -> None:
    time.sleep(random.uniform(config.MIN_DELAY_SECONDS, config.MAX_DELAY_SECONDS))


class FetchError(RuntimeError):
    pass


def get_with_retry(
    session: requests.Session,
    url: str,
    *,
    logger: logging.Logger,
    log_label: str,
    allow_redirects: bool = True,
    stream: bool = False,
    referer: Optional[str] = None,
) -> requests.Response:
    """GET with bounded retries + exponential backoff on transient failures.

    A "transient failure" is a network-level error, a timeout, or a 5xx
    response. Anything else (2xx, 3xx when allow_redirects=False, 4xx) is
    returned as-is for the caller to interpret.
    """
    headers = {"Referer": referer} if referer else {}
    last_error: Optional[Exception] = None

    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            polite_delay()
            response = session.get(
                url,
                headers=headers,
                allow_redirects=allow_redirects,
                stream=stream,
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code >= 500:
                raise FetchError(f"HTTP {response.status_code} from {url}")
            return response
        except (requests.RequestException, FetchError) as exc:
            last_error = exc
            logger.info(
                f"RETRY_ATTEMPT={attempt}/{config.MAX_RETRIES} | url={url} | "
                f"label={log_label} | error={exc}"
            )
            if attempt < config.MAX_RETRIES:
                backoff = config.RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                time.sleep(backoff)

    raise FetchError(f"Exhausted retries for {url} ({log_label}): {last_error}")
