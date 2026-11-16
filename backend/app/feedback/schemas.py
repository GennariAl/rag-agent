from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from app.base_schema import BaseSchema


class FeedbackRequest(BaseSchema):
    session_id: Annotated[str, Field()]
    # AG-UI run_id on the streaming transport; backend-generated uuid4 (from ChatResponse) on the JSON transport.
    message_id: Annotated[str | None, Field(default=None)]
    feedback: Annotated[Literal["positive", "negative"], Field()]
    # Only applicable to negative feedback; the frontend never sends it for positive feedback.
    comment: Annotated[str | None, Field(default=None)]

    @model_validator(mode="after")
    def _no_comment_on_positive(self) -> Self:
        if self.feedback == "positive" and self.comment is not None:
            raise ValueError("comment must be null when feedback is 'positive'")
        return self
