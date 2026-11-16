import pytest
from fastapi import HTTPException

from app.auth import Auth, AuthContext, _parse_groups_header
from app.settings import Settings
from tests.conftest import generate_test_token


def test_parse_groups_header():
    assert _parse_groups_header("group1,group2,group3") == [
        "group1",
        "group2",
        "group3",
    ]
    assert _parse_groups_header("group1, group2 , group3 ") == [
        "group1",
        "group2",
        "group3",
    ]
    assert _parse_groups_header("") == []
    assert _parse_groups_header("group1,,group3") == ["group1", "group3"]


def test_auth_success():
    settings = Settings.model_construct(oidc_client_id="test-client-id")
    auth = Auth()
    token = generate_test_token({"email": "user@example.com"})

    context = auth.authenticate(
        settings=settings,
        x_forwarded_access_token=token,
        x_forwarded_email="user@example.com",
        x_forwarded_groups="admin,viewer",
    )

    assert isinstance(context, AuthContext)
    assert context.email == "user@example.com"
    assert context.groups == ["admin", "viewer"]


def test_auth_with_group_filtering():
    settings = Settings.model_construct(oidc_client_id="test-client-id")
    auth = Auth(groups=["admin"])
    token = generate_test_token({"email": "user@example.com"})

    context = auth.authenticate(
        settings=settings,
        x_forwarded_access_token=token,
        x_forwarded_email="user@example.com",
        x_forwarded_groups="admin,other",
    )

    assert "admin" in context.groups


def test_auth_forbidden_wrong_client_id():
    settings = Settings.model_construct(oidc_client_id="test-client-id")
    auth = Auth()
    token = generate_test_token({"email": "user@example.com"}, client_id="wrong-id")

    with pytest.raises(HTTPException) as exc_info:
        auth.authenticate(
            settings=settings,
            x_forwarded_access_token=token,
            x_forwarded_email="user@example.com",
            x_forwarded_groups="some_group",
        )

    assert exc_info.value.status_code == 403


def test_auth_forbidden_groups_not_allowed():
    settings = Settings.model_construct(oidc_client_id="test-client-id")
    auth = Auth(groups=["admin"])
    token = generate_test_token({"email": "user@example.com"})

    with pytest.raises(HTTPException) as exc_info:
        auth.authenticate(
            settings=settings,
            x_forwarded_access_token=token,
            x_forwarded_email="user@example.com",
            x_forwarded_groups="viewer,reader",
        )

    assert exc_info.value.status_code == 403
