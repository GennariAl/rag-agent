from http import HTTPStatus
from typing import Any

from fastapi import status


class AppError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        detail: str,
        title: str | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.title = title or HTTPStatus(status_code).phrase
        self.code = code

    def to_problem_details(self, instance: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
            "instance": instance,
        }
        if self.code:
            payload["code"] = self.code

        return payload


class EntityNotFound(AppError):
    def __init__(self, entity_name: str) -> None:
        detail_template = "{entity_name} not found"
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail_template.format(entity_name=entity_name),
        )
