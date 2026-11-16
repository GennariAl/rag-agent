from dishka import Provider, Scope, provide, provide_all

from app.assistant.repository import AssistantLogRepository
from app.assistant.service import AssistantService
from app.evaluation.service import EvaluationService
from app.feedback.repository import FeedbackRepository
from app.knowledge_base.repository import KnowledgeBaseRepository
from app.userinfo.repository import UserInfoRepository


class RepositoryProvider(Provider):
    scope = Scope.REQUEST

    user_info = provide(UserInfoRepository)
    # APP scope: the repository is stateless (builds a fresh search config per query), so a
    # single shared instance is safe. This lets the assistant graph — which captures it — be
    # APP-scoped too (a single compiled graph shared across requests).
    knowledge_base = provide(KnowledgeBaseRepository, scope=Scope.APP)
    # Stateless (holds only the session factory); REQUEST scope by default is fine.
    assistant_log = provide(AssistantLogRepository)
    feedback = provide(FeedbackRepository)


class ServiceProvider(Provider):
    scope = Scope.REQUEST

    services = provide_all(
        AssistantService,
        EvaluationService,
    )
