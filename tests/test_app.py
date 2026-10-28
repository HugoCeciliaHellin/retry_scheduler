import os
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5433/retry_scheduler_test",
)

from app.config import MAX_ATTEMPTS
from app.database import SessionLocal, init_db
from app.main import app
from app.models import Job, JobStatus
from app.retry import Decision, decide
from app.worker import claim_next_job, process_next_job

NEW_JOB = {
    "url": "https://example.com/webhook",
    "payload": {"payment_id": "pay_123", "status": "failed"},
}

client = TestClient(app)


@pytest.fixture
def clean_db():
    init_db()
    with SessionLocal() as session:
        session.execute(delete(Job))
        session.commit()


def create_job():
    return client.post("/jobs/", json=NEW_JOB).json()["id"]


def get_job(job_id):
    return client.get(f"/jobs/{job_id}/").json()


def downstream(status_code):
    def handler(request):
        return httpx2.Response(status_code)

    return httpx2.Client(transport=httpx2.MockTransport(handler))


def skip_backoff(job_id):
    with SessionLocal() as session:
        session.get_one(Job, job_id).next_retry_at = datetime.now(UTC)
        session.commit()


def test_create_and_get_job(clean_db):
    response = client.post("/jobs/", json=NEW_JOB)
    assert response.status_code == 201
    assert response.json()["status"] == "pending"

    job_id = response.json()["id"]
    response = client.get(f"/jobs/{job_id}/")
    assert response.status_code == 200
    job = response.json()
    assert set(job) == {"id", "status", "attempts", "next_retry_at"}
    assert job["status"] == "pending"
    assert job["attempts"] == 0

    assert client.get("/jobs/job_does_not_exist/").status_code == 404


def test_retry_logic_and_backoff():
    now = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)

    assert decide(200, 1, now) == Decision(JobStatus.COMPLETED)
    assert decide(404, 1, now) == Decision(JobStatus.FAILED)

    # Transient errors go back to pending and wait 2s, 4s, 8s and 16s.
    for status_code in (503, 429, None):
        for attempts, wait in [(1, 2), (2, 4), (3, 8), (4, 16)]:
            expected = Decision(JobStatus.PENDING, now + timedelta(seconds=wait))
            assert decide(status_code, attempts, now) == expected

    assert decide(503, MAX_ATTEMPTS, now) == Decision(JobStatus.DEAD)


def test_worker_completes_successful_job(clean_db):
    job_id = create_job()

    process_next_job(downstream(200))

    job = get_job(job_id)
    assert job["status"] == "completed"
    assert job["attempts"] == 1


def test_worker_retries_transient_error_and_marks_dead(clean_db):
    job_id = create_job()
    failing = downstream(503)

    process_next_job(failing)
    job = get_job(job_id)
    assert job["status"] == "pending"
    assert job["attempts"] == 1
    assert datetime.fromisoformat(job["next_retry_at"]) > datetime.now(UTC)

    for _ in range(MAX_ATTEMPTS - 1):
        skip_backoff(job_id)
        process_next_job(failing)

    job = get_job(job_id)
    assert job["status"] == "dead"
    assert job["attempts"] == MAX_ATTEMPTS


def test_claim_next_job_avoids_duplicates(clean_db):
    job_id = create_job()

    job = claim_next_job()
    assert job is not None and job.id == job_id
    assert get_job(job_id)["status"] == "running"

    assert claim_next_job() is None