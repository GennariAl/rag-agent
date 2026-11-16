import logging
from typing import Annotated

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Header, HTTPException, status

from app.userinfo.repository import UserInfoRepository
from app.userinfo.schemas import UserInfoSchema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/userinfo")


@router.get("", response_model=UserInfoSchema)
@inject
async def userinfo_route(
    user_repository: FromDishka[UserInfoRepository],
    x_forwarded_access_token: Annotated[str, Header(include_in_schema=False)] = "",
):
    logger.debug(x_forwarded_access_token)
    userinfo = await user_repository.get_user_info(x_forwarded_access_token)
    if not userinfo:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    return userinfo
