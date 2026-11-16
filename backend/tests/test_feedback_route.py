from httpx import AsyncClient


async def test_submit_positive_feedback_returns_204(authenticated_client: AsyncClient):
    resp = await authenticated_client.post(
        "/feedback",
        json={"sessionId": "s1", "feedback": "positive"},
    )

    assert resp.status_code == 204


async def test_submit_negative_feedback_with_comment_returns_204(
    authenticated_client: AsyncClient,
):
    resp = await authenticated_client.post(
        "/feedback",
        json={"sessionId": "s1", "feedback": "negative", "comment": "answer was wrong"},
    )

    assert resp.status_code == 204


async def test_submit_feedback_with_message_id_returns_204(
    authenticated_client: AsyncClient,
):
    resp = await authenticated_client.post(
        "/feedback",
        json={"sessionId": "s1", "messageId": "run-abc123", "feedback": "positive"},
    )

    assert resp.status_code == 204


async def test_submit_feedback_invalid_value_returns_422(
    authenticated_client: AsyncClient,
):
    resp = await authenticated_client.post(
        "/feedback",
        json={"sessionId": "s1", "feedback": "neutral"},
    )

    assert resp.status_code == 422


async def test_submit_positive_feedback_with_comment_returns_422(
    authenticated_client: AsyncClient,
):
    """comment is not allowed for positive feedback."""
    resp = await authenticated_client.post(
        "/feedback",
        json={"sessionId": "s1", "feedback": "positive", "comment": "great answer"},
    )

    assert resp.status_code == 422


async def test_submit_feedback_requires_auth(client: AsyncClient):
    resp = await client.post(
        "/feedback",
        json={"sessionId": "s1", "feedback": "positive"},
    )

    assert resp.status_code == 401
