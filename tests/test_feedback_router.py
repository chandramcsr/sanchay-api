async def _signup(client, email, name):
    r = await client.post("/api/v1/auth/signup", json={"email": email, "password": "hunter2222", "display_name": name})
    body = r.json()
    return body["access_token"], body["user"]["id"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_submit_feedback_succeeds_and_returns_the_created_record(client):
    token, _ = await _signup(client, "alice-fb1@example.com", "Alice")
    r = await client.post(
        "/api/v1/feedback", headers=_auth(token),
        json={"category": "idea", "message": "It'd be great if...", "app_version": "10.81.1"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["category"] == "idea"
    assert body["message"] == "It'd be great if..."
    assert body["app_version"] == "10.81.1"
    assert "id" in body
    assert "created_at" in body


async def test_submit_feedback_works_without_an_app_version(client):
    token, _ = await _signup(client, "alice-fb2@example.com", "Alice")
    r = await client.post(
        "/api/v1/feedback", headers=_auth(token),
        json={"category": "general", "message": "Just wanted to say thanks"},
    )
    assert r.status_code == 201
    assert r.json()["app_version"] is None


async def test_submit_feedback_rejects_an_invalid_category(client):
    token, _ = await _signup(client, "alice-fb3@example.com", "Alice")
    r = await client.post(
        "/api/v1/feedback", headers=_auth(token),
        json={"category": "not-a-real-category", "message": "Test"},
    )
    assert r.status_code == 400


async def test_submit_feedback_rejects_an_empty_message(client):
    token, _ = await _signup(client, "alice-fb4@example.com", "Alice")
    r = await client.post(
        "/api/v1/feedback", headers=_auth(token),
        json={"category": "bug", "message": ""},
    )
    assert r.status_code == 422  # Pydantic's min_length=1 catches this before it reaches the service


async def test_submit_feedback_requires_authentication(client):
    r = await client.post(
        "/api/v1/feedback",
        json={"category": "bug", "message": "Something's broken"},
    )
    assert r.status_code in (401, 403)


async def test_submitted_feedback_ignores_a_client_supplied_identity_field(client):
    """
    Direct correctness check: the row's user_id/email_snapshot come
    from the authenticated session -- there's no user_id field in
    FeedbackCreateRequest at all, so a client can't spoof who
    submitted it. Full DB-level confirmation (the stored row actually
    has the real user's id and email) lives in test_feedback_service.py;
    this just confirms the request still succeeds and silently ignores
    an extra field rather than erroring or (worse) using it.
    """
    token, _ = await _signup(client, "alice-fb5@example.com", "Alice")
    r = await client.post(
        "/api/v1/feedback", headers=_auth(token),
        json={"category": "bug", "message": "Test", "user_id": "someone-elses-id"},
    )
    assert r.status_code == 201
