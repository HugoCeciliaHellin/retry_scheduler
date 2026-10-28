# Retry Scheduler

A small service that accepts jobs (a target URL and a JSON payload), stores them in PostgreSQL and delivers them from a background worker. Transient failures are retried with exponential backoff, permanent ones are marked as failed, and a job that keeps failing ends up dead. The API shows the current state of any job.

## Running it

`docker compose up --build` starts PostgreSQL on localhost:5433, the API on http://localhost:8000 (docs at `/docs`) and one worker.

To run the API and the worker locally instead (Python 3.11 or newer):

```bash
docker compose up -d --wait db
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app    # API
python -m app.worker    # worker, in a second terminal
```

Nothing listens on port 9, so this job fails on every attempt. It is retried after 2, 4, 8 and 16 seconds and is dead about 30 seconds after the first attempt:

```bash
curl -s -X POST http://localhost:8000/jobs/ -H "Content-Type: application/json" \
  -d '{"url": "http://localhost:9/", "payload": {"payment_id": "pay_123", "status": "failed"}}'
curl -s http://localhost:8000/jobs/<id>/
```

## Tests

```bash
docker compose up -d --wait db
pip install -r requirements.txt
docker compose exec db createdb -U postgres retry_scheduler_test   # first time only
pytest
```

The tests use their own database, so a running worker never takes their jobs.

## Design

- Lifecycle: `pending -> running -> completed | failed | dead`, and back to `pending` after a retryable error. `failed` means the request itself is wrong (a 400 or 404); `dead` means the downstream was still failing after `MAX_ATTEMPTS` attempts (5 by default, the first one included). `GET /jobs/?status=dead` lists them, and the `last_error` column (stored for debugging, not exposed) says why.
- When to retry: `decide()` in `app/retry.py` makes the call. 408, 429, 500, 502, 503, 504, timeouts and connection errors are retried after `BACKOFF_BASE_SECONDS * 2^(n-1)` seconds, capped at `BACKOFF_MAX_SECONDS`; anything else fails straight away. The settings are environment variables read in `app/config.py`.
- Duplicates: a worker claims a job with `SELECT ... FOR UPDATE SKIP LOCKED`, marks it `running` and commits before the HTTP call, so two workers never get the same job. Delivery is at least once, so every request carries an `Idempotency-Key` header with the job id.
- Several workers: `docker compose up -d --scale worker=3`, or more `python -m app.worker` processes. `SKIP LOCKED` makes each worker skip the rows another one has locked.
- Testing: `decide()` takes the current time as a parameter, so the rules are tested with a fixed clock. The worker tests use a real database and a fake downstream (`httpx2.MockTransport`), without sleeps.

## Assumptions and trade-offs

- Any 2xx means the job was delivered. Jobs are independent, with no ordering between them.
- PostgreSQL is the queue instead of RabbitMQ or Celery: one less component, and enough for an internal service.
- The worker polls every second and sends one job at a time. To go faster, add workers.
- No lease: if a worker dies mid-request, its job stays in `running` and has to be reset by hand.
- No jitter, so jobs that fail together retry together, and `Retry-After` is ignored.
- No authentication, since the brief leaves it out.

## With more time

- A lease (`locked_until`) so jobs left in `running` by a crashed worker are picked up again.
- Jitter, `Retry-After` support and an endpoint to requeue dead jobs.
- Metrics and an alert on dead jobs, and Alembic migrations instead of `create_all`.
