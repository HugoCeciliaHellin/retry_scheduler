import logging
import time

import httpx2
from sqlalchemy import select, update

from app.config import POLL_INTERVAL_SECONDS, REQUEST_TIMEOUT_SECONDS
from app.database import SessionLocal, init_db
from app.models import Job, JobStatus, utc_now
from app.retry import Decision, decide

logger = logging.getLogger("worker")


def claim_next_job() -> Job | None:
    with SessionLocal() as session:
        job = session.scalar(
            select(Job)
            .where(Job.status == JobStatus.PENDING, Job.next_retry_at <= utc_now())
            .order_by(Job.next_retry_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        job.status = JobStatus.RUNNING
        job.attempts += 1
        job.next_retry_at = None
        # Commit before the HTTP call, so no row lock or database connection is
        # held while we wait on the downstream.
        session.commit()
        return job


def send_request(client: httpx2.Client, job: Job) -> tuple[int | None, str]:
    try:
        # Same key on every retry of this job, so the receiver can drop duplicates.
        response = client.post(job.url, json=job.payload, headers={"Idempotency-Key": job.id})
    except httpx2.RequestError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    return response.status_code, f"HTTP {response.status_code}"


def save_result(job: Job, decision: Decision, outcome: str) -> None:
    with SessionLocal() as session:
        session.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(
                status=decision.status,
                next_retry_at=decision.next_retry_at,
                last_error=None if decision.status == JobStatus.COMPLETED else outcome,
            )
        )
        session.commit()


def process_next_job(client: httpx2.Client) -> bool:
    job = claim_next_job()
    if job is None:
        return False

    status_code, outcome = send_request(client, job)
    decision = decide(status_code, job.attempts, utc_now())
    save_result(job, decision, outcome)

    level = logging.INFO
    if decision.status in (JobStatus.FAILED, JobStatus.DEAD):
        level = logging.WARNING
    logger.log(level, "job=%s attempt=%d %s -> %s", job.id, job.attempts, outcome, decision.status)
    return True


def run() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # httpx2 logs every request at INFO with the full URL, which can carry tokens.
    logging.getLogger("httpx2").setLevel(logging.WARNING)

    init_db()
    logger.info("worker started")
    with httpx2.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        while True:
            try:
                processed = process_next_job(client)
            except Exception:
                logger.exception("error while processing jobs")
                processed = False
            if not processed:
                time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        logger.info("worker stopped")