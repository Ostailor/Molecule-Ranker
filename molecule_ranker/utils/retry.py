from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

import requests


class HttpResponseLike(Protocol):
    status_code: int

    def raise_for_status(self) -> None: ...


ResponseT = TypeVar("ResponseT", bound=HttpResponseLike)


class RetryHttpError(requests.HTTPError):
    def __init__(self, original: requests.HTTPError, retry_metadata: RetryMetadata) -> None:
        super().__init__(*original.args)
        self.retry_metadata = retry_metadata


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 2
    backoff_seconds: float = 0.5
    jitter_seconds: float = 0.1


@dataclass
class RetryMetadata:
    attempts: int = 0
    retry_count: int = 0
    rate_limit_retry_count: int = 0
    status_codes: list[int] = field(default_factory=list)


def request_with_retries(
    request: Callable[[], ResponseT],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[float], float] | None = None,
) -> tuple[ResponseT, RetryMetadata]:
    """Run a request with retries for transient 429 and 5xx responses only."""

    jitter_fn = jitter or (lambda upper: random.uniform(0, upper) if upper > 0 else 0.0)
    metadata = RetryMetadata()
    last_error: requests.RequestException | None = None

    for attempt in range(policy.max_retries + 1):
        metadata.attempts += 1
        try:
            response = request()
            status_code = int(getattr(response, "status_code", 200))
            metadata.status_codes.append(status_code)
            if _should_retry_status(status_code) and attempt < policy.max_retries:
                metadata.retry_count += 1
                if status_code == 429:
                    metadata.rate_limit_retry_count += 1
                sleep(_backoff_delay(policy, attempt, jitter_fn))
                continue
            response.raise_for_status()
            return response, metadata
        except requests.HTTPError as exc:
            raise RetryHttpError(exc, metadata) from exc
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = exc
            if attempt < policy.max_retries:
                metadata.retry_count += 1
                sleep(_backoff_delay(policy, attempt, jitter_fn))
                continue
            raise

    if last_error is not None:  # pragma: no cover - loop returns or raises first
        raise last_error
    raise RuntimeError("Retry loop exited unexpectedly.")  # pragma: no cover


def _should_retry_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _backoff_delay(
    policy: RetryPolicy,
    attempt: int,
    jitter: Callable[[float], float],
) -> float:
    base = max(policy.backoff_seconds, 0.0) * (2**attempt)
    return base + max(jitter(max(policy.jitter_seconds, 0.0)), 0.0)
