from typing import Annotated

from pydantic import Field

from app.base_schema import BaseSchema


class UserInfoSchema(BaseSchema):
    surname: Annotated[str, Field()]
    name: Annotated[str, Field()]
    email: Annotated[str, Field()]
    groups: Annotated[list[str], Field()]
