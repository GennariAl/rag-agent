from typing import Annotated

import jwt
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import Header, HTTPException, status
from pydantic import BaseModel

from app.errors import AppError
from app.settings import Settings


class AuthContext(BaseModel):
    email: str
    groups: list[str]

    def ensure_admin(self) -> None:
        if "okd_admin" not in self.groups:
            raise AppError(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: insufficient permissions",
            )

    def ensure_user_or_admin(self) -> None:
        if "okd_admin" not in self.groups and "okd_editor" not in self.groups:
            raise AppError(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: insufficient permissions",
            )


def _parse_groups_header(raw_groups: str) -> list[str]:
    return [group.strip() for group in raw_groups.split(",") if group.strip()]


class Auth:
    def __init__(self, groups: list[str] | None = None):
        self.groups = groups or []

    def authenticate(
        self,
        settings: Settings,
        x_forwarded_access_token: str,
        x_forwarded_email: str,
        x_forwarded_groups: str,
    ) -> AuthContext:
        decoded = jwt.decode(
            x_forwarded_access_token, options={"verify_signature": False}
        )
        client_id = decoded["client_id"]

        if client_id != settings.oidc_client_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

        user_groups = _parse_groups_header(x_forwarded_groups)

        if self.groups and not any(g in self.groups for g in user_groups):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

        return AuthContext(email=x_forwarded_email, groups=user_groups)

    @inject
    async def __call__(
        self,
        settings: FromDishka[Settings],
        x_forwarded_access_token: Annotated[str, Header(include_in_schema=False)] = "",
        x_forwarded_email: Annotated[str, Header(include_in_schema=False)] = "",
        x_forwarded_groups: Annotated[str, Header(include_in_schema=False)] = "",
    ) -> AuthContext:
        if settings.stage == "local":
            if self.groups and not any(
                group in self.groups for group in settings.local_user_groups
            ):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
            return AuthContext(
                email="local@example.com", groups=settings.local_user_groups
            )

        if not x_forwarded_access_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

        return self.authenticate(
            settings=settings,
            x_forwarded_access_token=x_forwarded_access_token,
            x_forwarded_email=x_forwarded_email,
            x_forwarded_groups=x_forwarded_groups,
        )
