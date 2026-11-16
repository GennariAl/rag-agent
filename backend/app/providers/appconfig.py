import json
import logging
from typing import Any, NewType

from dishka import Provider, Scope, provide
from httpx import AsyncClient

from app.appconfig_settings import AppConfigSettings
from app.settings import Settings

logger = logging.getLogger(__name__)

AppConfigFlags = NewType("AppConfigFlags", dict[str, Any])


class AppConfigProvider(Provider):
    scope = Scope.REQUEST

    @provide
    async def appconfig_flags(
        self,
        http: AsyncClient,
        settings: Settings,
        appconfig_settings: AppConfigSettings,
    ) -> AppConfigFlags:
        if settings.stage == "local":
            if not appconfig_settings.local_flags:
                return AppConfigFlags({})
            return AppConfigFlags(json.loads(appconfig_settings.local_flags))

        try:
            response = await http.get(
                f"http://localhost:2772/applications/{appconfig_settings.application_name}/environments/{appconfig_settings.environment_name}/configurations/{appconfig_settings.configuration_name}"
            )

            return response.json()
        except Exception:
            logger.exception("AppConfig fetch failed")
            return AppConfigFlags({})
