from fastapi import APIRouter, Depends, HTTPException

from api.auth import CurrentUser, current_user
from api.policy import assert_can, mentions_other_tenant
from api.schemas import ErrorResponse, JobCreate, JobCreated, JobView
from utils.runtime import enqueue_etl, get_backends

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def limited_user(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """Count this call against the user and tenant budgets before any model work."""
    _, limiter = get_backends()
    try:
        allowed = limiter.allow(user.user_id, user.tenant_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="rate limiter unavailable") from exc
    if not allowed:
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    return user


@router.post(
    "",
    status_code=202,
    response_model=JobCreated,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
)
def create_job(body: JobCreate, user: CurrentUser = Depends(limited_user)) -> dict:
    try:
        assert_can(user.role, "etl")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"{user.role} cannot run {exc}") from exc
    if mentions_other_tenant(body.question, user.tenant_id):
        raise HTTPException(status_code=403, detail="cross-tenant query")
    job_id = enqueue_etl(
        question=body.question,
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        role=user.role,
    )
    return {"job_id": job_id, "status": "queued"}


@router.get(
    "/{job_id}",
    response_model=JobView,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def read_job(job_id: str, user: CurrentUser = Depends(current_user)) -> dict:
    jobs, _ = get_backends()
    job = jobs.get(job_id)
    if job is None or job.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "job_id": job["id"],
        "status": job["status"],
        "result": job.get("result") or "",
        "error": job.get("error") or "",
    }
