import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_session, init_db
from app.models import Job, JobStatus
from app.schemas import CreateJobRequest, CreateJobResponse, JobResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("api")

SessionDep = Annotated[Session, Depends(get_session)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Retry Scheduler", lifespan=lifespan)


@app.post("/jobs/", response_model=CreateJobResponse, status_code=201)
def create_job(body: CreateJobRequest, session: SessionDep):
    job = Job(url=str(body.url), payload=body.payload)
    session.add(job)
    session.commit()
    # Never log the payload: it can carry payment or personal data.
    logger.info("job=%s created", job.id)
    return job


@app.get("/jobs/", response_model=list[JobResponse])
def list_jobs(
    session: SessionDep,
    status: JobStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
):
    query = select(Job).order_by(Job.updated_at.desc()).limit(limit)
    if status is not None:
        query = query.where(Job.status == status)
    return session.scalars(query).all()


@app.get("/jobs/{job_id}/", response_model=JobResponse)
def get_job(job_id: str, session: SessionDep):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/health")
def health(session: SessionDep):
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "ok"}