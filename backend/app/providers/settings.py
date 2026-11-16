from dishka import Provider, Scope, provide

from app.appconfig_settings import AppConfigSettings
from app.aws_settings import AWSSettings
from app.database_settings import DatabaseSettings
from app.llm_gw_settings import LLMGWSettings
from app.settings import Settings


class SettingsProvider(Provider):
    scope = Scope.APP

    @provide
    def settings(self) -> Settings:
        return Settings()  # type: ignore

    @provide
    def database_settings(self) -> DatabaseSettings:
        return DatabaseSettings()  # type: ignore

    @provide
    def aws_settings(self) -> AWSSettings:
        return AWSSettings()  # type: ignore

    @provide
    def appconfig_settings(self) -> AppConfigSettings:
        return AppConfigSettings()  # type: ignore

    @provide
    def llm_gw_settings(self) -> LLMGWSettings:
        return LLMGWSettings()  # type: ignore
