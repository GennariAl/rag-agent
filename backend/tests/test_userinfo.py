import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_userinfo_success(client: AsyncClient):
    response = await client.get(
        "/userinfo", headers={"x-forwarded-access-token": "valid-token"}
    )

    assert response.status_code == 200
    json_response = response.json()
    assert json_response["surname"] == "Doe"
    assert json_response["name"] == "John"
    assert json_response["email"] == "john.doe@example.com"


@pytest.mark.asyncio
async def test_userinfo_invalid_token(client: AsyncClient):
    response = await client.get(
        "/userinfo", headers={"x-forwarded-access-token": "invalid-token"}
    )
    assert response.status_code == 401
