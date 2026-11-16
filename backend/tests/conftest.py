from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import aioboto3
import jwt
import pytest_asyncio
from ag_ui.core import EventType
from ag_ui.core.events import (
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
)
from dishka import (
    AsyncContainer,
    Provider,
    Scope,
    make_async_container,
    provide,
)
from dishka.integrations.fastapi import FastapiProvider
from httpx import ASGITransport, AsyncClient
from moto.server import ThreadedMotoServer
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.appconfig_settings import AppConfigSettings
from app.assistant.schemas import ChatRequest, ChatResponse
from app.assistant.service import AssistantService
from app.aws_settings import AWSSettings
from app.database_settings import DatabaseSettings
from app.evaluation.service import EvaluationService
from app.llm_gw_settings import LLMGWSettings
from app.main import create_app
from app.providers.appconfig import AppConfigFlags
from app.providers.assistant import AssistantProvider
from app.providers.aws import AWSProvider
from app.providers.database import DatabaseProvider
from app.providers.knowledge_base import KnowledgeBaseProvider
from app.providers.llm import EvaluationProvider, LLMProvider
from app.providers.service import RepositoryProvider, ServiceProvider
from app.settings import Settings
from app.userinfo.repository import UserInfoRepository
from app.userinfo.schemas import UserInfoSchema

TEST_CLIENT_ID = "test-client-id"


def generate_test_token(user_data: dict, client_id: str = TEST_CLIENT_ID) -> str:
    payload = {
        "client_id": client_id,
        "email": user_data.get("email", ""),
        "name": user_data.get("name", ""),
        "surname": user_data.get("surname", ""),
    }
    return jwt.encode(payload, "test-secret-key-padded-to-32-bytes", algorithm="HS256")


def auth_headers(token_key: str) -> dict[str, str]:
    user_data = TOKEN_FIXTURES[token_key]
    return {
        "x-forwarded-access-token": generate_test_token(user_data),
        "x-forwarded-email": user_data["email"],
        "x-forwarded-groups": ",".join(user_data["groups"]),
    }


TOKEN_FIXTURES = {
    "valid-token": {
        "surname": "Doe",
        "name": "John",
        "email": "john.doe@example.com",
        "groups": ["okd_admin", "okd_editor"],
    },
    "viewer-token": {
        "surname": "Smith",
        "name": "Jane",
        "email": "jane.smith@example.com",
        "groups": ["okd_viewer"],
    },
}


class MockUserInfoRepository(UserInfoRepository):
    def __init__(self):
        self.remote_users = {
            token: UserInfoSchema(**payload)
            for token, payload in TOKEN_FIXTURES.items()
        }

    async def get_user_info(self, access_token: str) -> UserInfoSchema | None:
        return self.remote_users.get(access_token)


class MockUserInfoRepositoryProvider(Provider):
    scope = Scope.APP

    service = provide(MockUserInfoRepository, provides=UserInfoRepository)


def basic_stream_events():
    """Default streaming scenario: a normal chat-model stream (start → one delta → finish)."""
    return [
        RunStartedEvent(type=EventType.RUN_STARTED, thread_id="s-stream", run_id="r1"),
        TextMessageContentEvent(
            type=EventType.TEXT_MESSAGE_CONTENT, message_id="m1", delta="Hello"
        ),
        RunFinishedEvent(
            type=EventType.RUN_FINISHED, thread_id="s-stream", run_id="r1"
        ),
    ]


class MockAssistantService(AssistantService):
    """Stand-in for the real assistant: skips the LangGraph run (which needs the LLM
    Gateway + populated KB) and returns canned output. Lets the route tests exercise the
    auth gate and both transports without standing up the graph.

    `chat` returns a canned `ChatResponse`. `stream` yields a canned AG-UI event list
    (class attr `stream_events`, overridable per test — default: a normal chat-model stream)
    and records the forwarded email (`last_user_email`) so tests can assert it reached the run
    — never the client payload. Both are class attrs because this service is REQUEST-scoped
    (a fresh instance per request), so the observable state must not live on the instance."""

    stream_events = basic_stream_events()
    last_user_email: str | None = None

    def __init__(self):
        pass

    async def chat(self, request: ChatRequest, user_email: str) -> ChatResponse:
        return ChatResponse(
            answer="canned answer",
            references=[],
            rephrased_question=request.message,
            session_id=request.session_id,
            message_id="canned-message-id",
        )

    async def stream(self, input_data, user_email: str):
        type(self).last_user_email = user_email
        for event in self.stream_events:
            yield event


class MockEvaluationService(EvaluationService):
    """Stand-in for the real batch evaluation: skips the assistant + judge LLM runs and
    returns a canned CSV. Lets the route tests exercise the auth gate without standing up
    the graph or the judge model."""

    def __init__(self):
        pass

    async def evaluate(self, csv_bytes: bytes) -> bytes:
        return b"canned,csv\n"


class MockAppConfigProvider(Provider):
    scope = Scope.REQUEST

    @provide
    async def appconfig_flags(
        self, http: AsyncClient, settings: AppConfigSettings
    ) -> AppConfigFlags:
        return AppConfigFlags({})


class LocalHttpProvider(Provider):
    scope = Scope.APP

    @provide
    async def http(self) -> AsyncGenerator[AsyncClient, Any]:
        async with AsyncClient() as client:
            yield client


@pytest_asyncio.fixture(scope="session", autouse=True)
async def moto_server():
    server = ThreadedMotoServer(ip_address="localhost")
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_aws_resources(moto_server: ThreadedMotoServer):
    region = "eu-central-1"
    host, port = moto_server.get_host_and_port()
    endpoint = f"http://{host}:{port}"

    session = aioboto3.Session(
        aws_access_key_id="test",
        aws_secret_access_key="test",
        region_name=region,
    )

    bucket_name = f"okd-tests-{uuid4().hex}"
    async with session.client("s3", endpoint_url=endpoint) as s3_client:
        await s3_client.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )

    yield {
        "region": region,
        "endpoint": endpoint,
        "stage": "test",
        "bucket": bucket_name,
    }


@pytest_asyncio.fixture(scope="function")
async def database_provider(async_session: AsyncSession):
    class TestDatabaseProvider(Provider):
        @provide(scope=Scope.APP)
        async def async_session(self) -> AsyncGenerator[AsyncSession, Any]:
            yield async_session

        # Needed by KnowledgeBaseProvider.pg_engine (PGEngine.from_engine). Lazy: no test
        # resolves the PGVectorStore, so this engine is never actually connected — it only
        # satisfies the dependency graph.
        @provide(scope=Scope.APP)
        def async_engine(self, database_settings: DatabaseSettings) -> AsyncEngine:
            return create_async_engine(str(database_settings.database_uri))

        # Needed by AssistantLogRepository (opens its own session to persist the log). Lazy:
        # the route tests use MockAssistantService, so the real repository is never resolved —
        # this only satisfies the dependency graph at container build time.
        @provide(scope=Scope.APP)
        def session_factory(
            self, engine: AsyncEngine
        ) -> async_sessionmaker[AsyncSession]:
            return async_sessionmaker(engine)

    return TestDatabaseProvider()


@pytest_asyncio.fixture(scope="function")
async def async_session(settings_provider) -> AsyncGenerator[AsyncSession, Any]:
    db_container = make_async_container(
        settings_provider,
        DatabaseProvider(),
    )

    session_factory = await db_container.get(async_sessionmaker[AsyncSession])
    async with session_factory() as session, session.begin():
        yield session
        await session.rollback()

    await db_container.close()


@pytest_asyncio.fixture(scope="function")
async def session_factory(
    settings_provider,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], Any]:
    """The real session factory, for tests that need their own committed session.

    Unlike `async_session` (one rolled-back transaction for isolation), this hands out the
    `async_sessionmaker` so a test can exercise code that opens its own session and commits —
    e.g. `AssistantLogRepository`, which writes the log on an independent session on purpose.
    """
    db_container = make_async_container(settings_provider, DatabaseProvider())
    yield await db_container.get(async_sessionmaker[AsyncSession])
    await db_container.close()


@pytest_asyncio.fixture(scope="session")
async def settings_provider(setup_aws_resources):
    aws_settings = AWSSettings.model_construct(
        stage=setup_aws_resources["stage"],
        aws_region=setup_aws_resources["region"],
        aws_endpoint_url=setup_aws_resources["endpoint"],
    )
    app_settings = Settings.model_construct(
        stage="dev", oidc_client_id="test-client-id"
    )

    appconfig_settings = AppConfigSettings.model_construct(
        application_name="test",
        environment_name="test",
        configuration_name="test",
    )

    llm_gw_settings = LLMGWSettings.model_construct(
        stage="dev",
        llm_gw_key="test-key",
    )

    class TestSettingsProvider(Provider):
        scope = Scope.APP

        @provide
        async def aws_settings(self) -> AWSSettings:
            return aws_settings

        @provide
        def database_settings(self) -> DatabaseSettings:
            return DatabaseSettings()  # type: ignore

        @provide
        async def settings(self) -> Settings:
            return app_settings

        @provide
        async def appconfig_settings(self) -> AppConfigSettings:
            return appconfig_settings

        @provide
        async def llm_gw_settings(self) -> LLMGWSettings:
            return llm_gw_settings

    return TestSettingsProvider()


@pytest_asyncio.fixture(scope="function")
async def app_container(settings_provider, database_provider):
    repository_provider = RepositoryProvider()
    repository_provider.provide(MockUserInfoRepository, provides=UserInfoRepository)

    service_provider = ServiceProvider()
    service_provider.provide(MockAssistantService, provides=AssistantService)
    service_provider.provide(MockEvaluationService, provides=EvaluationService)

    container = make_async_container(
        settings_provider,
        AWSProvider(),
        LocalHttpProvider(),
        database_provider,
        KnowledgeBaseProvider(),
        LLMProvider(),
        EvaluationProvider(),
        AssistantProvider(),
        repository_provider,
        service_provider,
        MockAppConfigProvider(),
        FastapiProvider(),
    )

    yield container

    await container.close()


@pytest_asyncio.fixture(scope="function")
async def container(app_container: AsyncContainer):
    async with app_container() as request_container:
        yield request_container


@pytest_asyncio.fixture(scope="function")
async def client(app_container: AsyncContainer) -> AsyncGenerator[AsyncClient, Any]:
    app = create_app(app_container)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost:3000"
    ) as client:
        yield client


@pytest_asyncio.fixture
async def authenticated_client(request, client: AsyncClient):
    user = getattr(request, "param", "valid-token")

    original_headers = client.headers.copy()

    user_data = TOKEN_FIXTURES.get(user, TOKEN_FIXTURES["valid-token"])
    email = user_data["email"]
    groups_header = ",".join(user_data["groups"])

    token = generate_test_token(user_data)

    client.headers.update(
        {
            "x-forwarded-access-token": token,
            "x-forwarded-email": email,
            "x-forwarded-groups": groups_header,
        }
    )

    yield client

    client.headers.clear()
    client.headers.update(original_headers)
