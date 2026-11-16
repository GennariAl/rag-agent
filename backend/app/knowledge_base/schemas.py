from typing import Annotated, Any

from langchain_core.documents import Document
from pydantic import Field, model_validator

from app.base_schema import BaseSchema


class SearchQuery(BaseSchema):
    query: Annotated[str, Field()]
    top_k: Annotated[int | None, Field(default=None, ge=1)]
    candidates: Annotated[int | None, Field(default=None, ge=1)]
    category: Annotated[str | None, Field(default=None)]


class SearchResult(BaseSchema):
    content: Annotated[str, Field()]
    document_title: Annotated[str | None, Field(default=None)]
    category: Annotated[str | None, Field(default=None)]
    chunk_index: Annotated[int | None, Field(default=None)]
    permalink: Annotated[str | None, Field(default=None)]
    page_number: Annotated[int | None, Field(default=None)]
    metadata: Annotated[dict[str, Any], Field(default_factory=dict)]

    @model_validator(mode="before")
    @classmethod
    def _flatten_document(cls, data: Any) -> Any:
        """Map a langchain Document onto the DTO fields.

        A Document carries `page_content` plus a flat `metadata` dict; we expose
        `content` and lift the known metadata keys to top-level fields (keeping the full
        dict in `metadata`). Anything that is not a Document is passed through unchanged.
        """
        if isinstance(data, Document):
            metadata = data.metadata or {}
            return {**metadata, "content": data.page_content, "metadata": metadata}
        return data
