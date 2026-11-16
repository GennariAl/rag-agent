from dishka import Provider, Scope, provide
from langchain_openai import AzureOpenAIEmbeddings
from langchain_postgres import PGEngine, PGVectorStore
from langchain_postgres.v2.hybrid_search_config import (
    HybridSearchConfig,
    reciprocal_rank_fusion,
)
from sqlalchemy.ext.asyncio import AsyncEngine

from app.knowledge_base.models import (
    EMBEDDING_DEPLOYMENT,
    METADATA_NAMES,
    RRF_K,
    TABLE_NAME,
    TSV_COLUMN,
    TSV_INDEX,
)
from app.llm_gw_settings import LLMGWSettings


class KnowledgeBaseProvider(Provider):
    scope = Scope.APP

    @provide
    def embedding_client(self, llm_gw_settings: LLMGWSettings) -> AzureOpenAIEmbeddings:
        return AzureOpenAIEmbeddings(
            azure_endpoint=f"{llm_gw_settings.endpoint}/foundry",
            api_key=llm_gw_settings.llm_gw_key,
            api_version=llm_gw_settings.api_version,
            azure_deployment=EMBEDDING_DEPLOYMENT,
            # The OpenAI client natively handles 429s with backoff and respects Azure's
            # Retry-After; we raise the number of attempts to absorb the rate limits.
            max_retries=6,
        )

    @provide
    def pg_engine(self, async_engine: AsyncEngine) -> PGEngine:
        # Reuse the single AsyncEngine managed by DatabaseProvider (one connection pool)
        # instead of opening a separate one via from_connection_string.
        return PGEngine.from_engine(engine=async_engine)

    @provide
    async def vectorstore(
        self, pg_engine: PGEngine, embedding_client: AzureOpenAIEmbeddings
    ) -> PGVectorStore:
        # Connect to the existing table (created by the Alembic migration). The hybrid config
        # set here only fixes the tsvector column/index for the full-text branch at
        # table-connect time; the per-query config (built fresh in KnowledgeBaseRepository) is
        # the one actually passed on each search.
        return await PGVectorStore.create(
            engine=pg_engine,
            table_name=TABLE_NAME,
            embedding_service=embedding_client,
            metadata_columns=METADATA_NAMES,
            hybrid_search_config=HybridSearchConfig(
                tsv_column=TSV_COLUMN,
                tsv_lang="pg_catalog.english",
                fusion_function=reciprocal_rank_fusion,
                fusion_function_parameters={"rrf_k": RRF_K},
                index_name=TSV_INDEX,
                index_type="GIN",
            ),
        )
