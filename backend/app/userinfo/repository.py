import logging

from httpx import AsyncClient

from app.settings import Settings
from app.userinfo.schemas import UserInfoSchema

logger = logging.getLogger(__name__)


class UserInfoRepository:
    def __init__(self, http: AsyncClient, settings: Settings):
        self.http = http
        self.settings = settings
        self.oidc_issuer_url = settings.oidc_issuer_url

    async def get_user_info(self, access_token: str) -> UserInfoSchema | None:
        if self.settings.stage == "local":
            return UserInfoSchema(
                email="local@example.com",
                name="Local",
                surname="User",
                groups=self.settings.local_user_groups,
            )

        response = await self.http.get(
            f"{self.oidc_issuer_url}/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code != 200:
            return None

        return UserInfoSchema.model_validate(response.json())
