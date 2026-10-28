from datetime import datetime, timedelta
from typing import NamedTuple

from app.config import BACKOFF_BASE_SECONDS, BACKOFF_MAX_SECONDS, MAX_ATTEMPTS
from app.models import JobStatus


RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class Decision(NamedTuple):
    status: JobStatus
    next_retry_at: datetime | None = None


def backoff_seconds(attempts: int) -> float:
    # Doubles after every failure (2s, 4s, 8s, 16s with the defaults) so a
    # struggling service gets more room each time.
    return min(BACKOFF_BASE_SECONDS * 2 ** (attempts - 1), BACKOFF_MAX_SECONDS)


def is_retryable(status_code: int | None) -> bool:
    return status_code is None or status_code in RETRYABLE_STATUS_CODES


def decide(status_code: int | None, attempts: int, now: datetime) -> Decision:
    if status_code is not None and 200 <= status_code < 300:
        return Decision(JobStatus.COMPLETED)
    if not is_retryable(status_code):
        return Decision(JobStatus.FAILED)
    # attempts already counts the attempt that just failed.
    if attempts >= MAX_ATTEMPTS:
        return Decision(JobStatus.DEAD)
    return Decision(JobStatus.PENDING, now + timedelta(seconds=backoff_seconds(attempts)))