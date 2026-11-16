from __future__ import annotations

from typing import NewType

from dishka import Provider, Scope, provide
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from app.agent.agent import AgentService, build_agent_graph
from app.knowledge_base.repository import KnowledgeBaseRepository
from app.providers.llm import MediumChatModel

AgentCheckpointer = NewType("AgentCheckpointer", InMemorySaver)
AgentGraph = NewType("AgentGraph", CompiledStateGraph)


class AgentProvider(Provider):
    @provide(scope=Scope.APP)
    def agent_checkpointer(self) -> AgentCheckpointer:
        return AgentCheckpointer(InMemorySaver())

    @provide(scope=Scope.APP)
    def agent_graph(
        self,
        agent_llm: MediumChatModel,
        judge_llm: MediumChatModel,
        kb_repository: KnowledgeBaseRepository,
        checkpointer: AgentCheckpointer,
    ) -> AgentGraph:
        return AgentGraph(
            build_agent_graph(agent_llm, judge_llm, kb_repository, checkpointer)
        )

    @provide(scope=Scope.APP)
    def agent_service(self, graph: AgentGraph) -> AgentService:
        return AgentService(graph)
