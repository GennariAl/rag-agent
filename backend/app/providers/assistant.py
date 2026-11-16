from dishka import Provider, Scope, provide
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from app.assistant.graph import build_graph
from app.knowledge_base.repository import KnowledgeBaseRepository
from app.providers.llm import MediumChatModel, SmallChatModel


class AssistantProvider(Provider):
    @provide(scope=Scope.APP)
    def checkpointer(self) -> InMemorySaver:
        # In-memory conversation memory for the PoC: keyed by session_id (= thread_id),
        # shared across requests (APP scope), lost on restart and not shared across workers.
        # Aurora persistence is a later step.
        return InMemorySaver()

    @provide(scope=Scope.APP)
    def graph(
        self,
        small_model: SmallChatModel,
        medium_model: MediumChatModel,
        kb_repository: KnowledgeBaseRepository,
        checkpointer: InMemorySaver,
    ) -> CompiledStateGraph:
        # APP scope: a single compiled graph shared across requests. It captures only
        # APP-scoped dependencies (the models, the stateless knowledge-base repository and the
        # in-memory checkpointer); per-turn state lives in the checkpointer keyed by thread_id,
        # so one compiled graph is safe to share. Tier->node mapping lives here (the only place
        # that knows both the tiers and the graph shape): orchestrate and rephrase run on the
        # small tier (Haiku, same instance reused), judge and chat on the medium tier (Sonnet).
        return build_graph(
            orchestrator_llm=small_model,
            rephrase_llm=small_model,
            judge_llm=medium_model,
            chat_llm=medium_model,
            kb_repository=kb_repository,
            checkpointer=checkpointer,
        )
