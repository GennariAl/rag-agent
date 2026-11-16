import logging
from contextlib import asynccontextmanager
from http import HTTPStatus

import asyncpg
from dishka import AsyncContainer, FromDishka, make_async_container
from dishka.integrations.fastapi import (
    FastapiProvider,
    inject,
    setup_dishka,
)
from fastapi import APIRouter, Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import (
    get_swagger_ui_html,
)
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app.agent.routes import router as agent_router
from app.assistant.routes import router as assistant_router
from app.errors import AppError
from app.feedback.routes import router as feedback_router
from app.health.routes import router as health_router
from app.providers.agent import AgentProvider
from app.providers.appconfig import AppConfigFlags, AppConfigProvider
from app.providers.assistant import AssistantProvider
from app.providers.aws import AWSProvider
from app.providers.database import DatabaseProvider
from app.providers.http import HttpProvider
from app.providers.knowledge_base import KnowledgeBaseProvider
from app.providers.llm import EvaluationProvider, LLMProvider
from app.providers.service import RepositoryProvider, ServiceProvider
from app.providers.settings import SettingsProvider
from app.settings import Settings
from app.userinfo.routes import router as userinfo_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await app.state.dishka_container.close()


class LoggingLevel:
    @inject
    async def __call__(self, flags: FromDishka[AppConfigFlags]) -> None:
        logging_settings = flags.get("logging_settings", {})
        if logging_settings.get("enabled"):
            logging.getLogger().setLevel(logging_settings.get("level"))
        else:
            logging.getLogger().setLevel(logging.INFO)


def _problem_details_payload(
    *,
    request: Request,
    status_code: int,
    detail: str,
    title: str | None = None,
    errors: object | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": title or HTTPStatus(status_code).phrase,
        "status": status_code,
        "detail": detail,
        "instance": request.url.path,
    }
    if errors is not None:
        payload["errors"] = errors
    return payload


def create_app(container: AsyncContainer) -> FastAPI:
    settings = Settings()  # type: ignore

    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        force=True,
    )

    class EndpointFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return (
                record.args  # type: ignore
                and len(record.args) >= 3
                and record.args[2] not in ["/health"]  # type: ignore
            )

    logging.getLogger("uvicorn.access").addFilter(EndpointFilter())

    app = FastAPI(
        lifespan=lifespan,
        redirect_slashes=False,
        docs_url=None,
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
    )

    app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.get("/docs", include_in_schema=False)
    async def custom_swagger_ui_html():
        return get_swagger_ui_html(
            openapi_url=app.openapi_url,  # type: ignore
            title=app.title + " - Swagger UI",
            oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
            swagger_js_url="/static/swagger-ui-bundle.js",
            swagger_css_url="/static/swagger-ui.css",
            swagger_favicon_url="",
        )

    app.add_middleware(
        CORSMiddleware,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
        allow_origins=settings.cors_origins,
    )

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, error: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=error.to_problem_details(request.url.path),
        )

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_error_handler(
        request: Request, error: SQLAlchemyError
    ) -> JSONResponse:
        logging.getLogger(__name__).exception("Unhandled database exception")
        match getattr(error, "orig", error):
            case asyncpg.UniqueViolationError():
                status_code, detail = (
                    status.HTTP_409_CONFLICT,
                    "Resource with this name already exists",
                )
            case asyncpg.NotNullViolationError():
                status_code, detail = (
                    status.HTTP_400_BAD_REQUEST,
                    "A required field is missing",
                )
            case asyncpg.ForeignKeyViolationError():
                status_code, detail = (
                    status.HTTP_400_BAD_REQUEST,
                    "A referenced resource does not exist",
                )
            case asyncpg.CheckViolationError():
                status_code, detail = (
                    status.HTTP_400_BAD_REQUEST,
                    "Invalid value for one or more fields",
                )
            case asyncpg.StringDataRightTruncationError():
                status_code, detail = (
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    "A field is too long, please shorten it",
                )
            case _:
                status_code, detail = (
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "Internal server error",
                )
        return JSONResponse(
            status_code=status_code,
            content=_problem_details_payload(
                request=request,
                status_code=status_code,
                detail=detail,
            ),
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, _: Exception) -> JSONResponse:
        logging.getLogger(__name__).exception("Unhandled exception")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_problem_details_payload(
                request=request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error",
            ),
        )

    app.include_router(health_router)

    api_router = APIRouter(
        dependencies=[Depends(LoggingLevel())],
    )

    api_router.include_router(userinfo_router)
    api_router.include_router(assistant_router)
    api_router.include_router(feedback_router)
    api_router.include_router(agent_router)
    app.include_router(api_router)

    setup_dishka(container, app)
    return app


providers = [
    HttpProvider(),
    AWSProvider(),
    SettingsProvider(),
    DatabaseProvider(),
    KnowledgeBaseProvider(),
    LLMProvider(),
    EvaluationProvider(),
    AssistantProvider(),
    AgentProvider(),
    RepositoryProvider(),
    ServiceProvider(),
    AppConfigProvider(),
    FastapiProvider(),
]


container = make_async_container(*providers)
app = create_app(container)
