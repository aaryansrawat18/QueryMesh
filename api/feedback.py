from fastapi import APIRouter, Depends, HTTPException

from api.auth import CurrentUser
from api.jobs import limited_user
from api.schemas import ErrorResponse, FeedbackRequest, FeedbackResponse
from utils.audit import find_request
from utils.feedback import store

router = APIRouter(prefix="/api/v1", tags=["feedback"])


@router.post(
    "/feedback",
    status_code=201,
    response_model=FeedbackResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
)
def post_feedback(body: FeedbackRequest, user: CurrentUser = Depends(limited_user)) -> dict:
    row = find_request(body.request_id, user.tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="request not found")
    store(
        request_id=body.request_id,
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        rating=body.rating,
        comment=body.comment,
        question=str(row.get("question") or ""),
    )
    return {"request_id": body.request_id, "rating": body.rating, "stored": True}
