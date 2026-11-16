import logging
from importlib import resources
from typing import ClassVar

import yaml
from langchain_postgres import PGVectorStore
from langchain_postgres.v2.hybrid_search_config import (
    HybridSearchConfig,
    reciprocal_rank_fusion,
)

from app.knowledge_base.models import RRF_K, TSV_COLUMN, TSV_INDEX
from app.knowledge_base.schemas import SearchQuery, SearchResult

logger = logging.getLogger(__name__)


class KnowledgeBaseRepository:
    """Data access for the knowledge base vector store (hybrid search).

    Stateless: it holds only the shared (APP-scoped) `PGVectorStore` and builds a fresh
    `HybridSearchConfig` per query, so a single instance is safe to share across concurrent
    requests (APP scope).
    """

    @staticmethod
    def _load_retrieval_config() -> dict[str, int]:
        """Load the packaged default hybrid-search parameters (top_k / candidates).

        Read once at import from `retrieval.yaml` (packaged data, via importlib.resources),
        mirroring how the assistant loads its prompts — so the retriever's tuning knobs can be
        edited without touching Python. Applied by `hybrid_search` when a `SearchQuery` leaves
        `top_k`/`candidates` unset.
        """
        _required = {"top_k", "candidates"}
        raw = yaml.safe_load(
            resources.files(__package__).joinpath("retrieval.yaml").read_text("utf-8")
        )
        missing = _required - raw.keys()
        if missing:
            raise ValueError(f"retrieval.yaml: missing required keys: {missing}")
        return {key: int(raw[key]) for key in _required}

    _RETRIEVAL: ClassVar[dict[str, int]] = _load_retrieval_config.__func__()  # type: ignore[attr-defined]
    _DEFAULT_TOP_K: ClassVar[int] = _RETRIEVAL["top_k"]
    _DEFAULT_CANDIDATES: ClassVar[int] = _RETRIEVAL["candidates"]

    def __init__(self, vectorstore: PGVectorStore):
        self.vectorstore = vectorstore

    @staticmethod
    def _make_hybrid_config(top_k: int, candidates: int) -> HybridSearchConfig:
        """Native hybrid-search config (tsvector full-text + RRF fusion), fresh per query.

        A new instance per call is required: `asimilarity_search` mutates `fts_query` with the
        current query text, so a shared instance would leak stale text across concurrent
        queries. `candidates` is the per-branch (dense/full-text) fetch before the RRF fusion;
        `top_k` the final count after fusion.
        """
        return HybridSearchConfig(
            tsv_column=TSV_COLUMN,
            tsv_lang="pg_catalog.english",
            fusion_function=reciprocal_rank_fusion,
            fusion_function_parameters={"rrf_k": RRF_K, "fetch_top_k": top_k},
            primary_top_k=candidates,
            secondary_top_k=candidates,
            index_name=TSV_INDEX,
            index_type="GIN",
        )

    async def hybrid_search(self, search: SearchQuery) -> list[SearchResult]:
        """Hybrid search: vector + full-text fused with RRF on the DB side.

        Builds a fresh config per call (no shared mutable state), so the repository stays
        stateless and APP-safe. The query's `top_k` (final result count) and `candidates`
        (per-branch candidates fetched before RRF) drive the config; when the `SearchQuery`
        leaves them unset they fall back to the packaged defaults (`retrieval.yaml`). Maps the
        langchain Documents to DTOs directly. This config overwrite the vectorstore provider
        config.
        """
        top_k = search.top_k if search.top_k is not None else self._DEFAULT_TOP_K
        candidates = (
            search.candidates
            if search.candidates is not None
            else self._DEFAULT_CANDIDATES
        )
        hybrid_config = self._make_hybrid_config(top_k, candidates)
        filter = (
            {"category": search.category} if search.category else None
        )  # NOTE: category not provided in this phase
        docs = await self.vectorstore.asimilarity_search(
            search.query,
            k=top_k,
            filter=filter,
            hybrid_search_config=hybrid_config,
        )
        return [SearchResult.model_validate(doc) for doc in docs]
