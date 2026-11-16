from typing import Annotated

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Depends, status
from fastapi.responses import Response

from app.auth import Auth, AuthContext
from app.feedback.repository import FeedbackRepository
from app.feedback.schemas import FeedbackRequest

router = APIRouter(prefix="/feedback")


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def submit_feedback(
    request: FeedbackRequest,
    repository: FromDishka[FeedbackRepository],
    auth: Annotated[AuthContext, Depends(Auth())],
) -> Response:
    await repository.save(request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
