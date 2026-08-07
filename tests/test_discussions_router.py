async def _create_discussion(client, clerk_auth, user="user_alice", **overrides):
    payload = {
        "title": "Weekly sync",
        "transcript": "We decided to use Postgres. Alice will review the schema.",
        "analysis": {
            "summary": ["We decided to use Postgres."],
            "decisions": ["We decided to use Postgres."],
            "actions": [{"owner": "Alice", "task": "Alice will review the schema."}],
            "risks": [],
            "suggestions": [],
            "questions": [],
            "sentiment": {"pos": 60, "neu": 40, "neg": 0},
        },
        "duration_seconds": 125,
    }
    payload.update(overrides)
    return await client.post("/api/v1/discussions", headers=clerk_auth(user), json=payload)


async def test_create_discussion(client, clerk_auth):
    r = await _create_discussion(client, clerk_auth)
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "Weekly sync"
    assert body["duration_seconds"] == 125
    assert body["analysis"]["decisions"] == ["We decided to use Postgres."]
    assert body["analysis"]["actions"][0]["owner"] == "Alice"


async def test_create_discussion_without_analysis(client, clerk_auth):
    """A discussion saved straight from a pasted transcript with no
    analysis run -- a real path in the recorder UI, not hypothetical."""
    r = await _create_discussion(client, clerk_auth, analysis=None)
    assert r.status_code == 201
    assert r.json()["analysis"] is None


async def test_list_omits_transcript_and_analysis(client, clerk_auth):
    """List view is deliberately lighter than detail view -- matches
    ledger-app's history list (title/date/duration only, full detail
    loads on expand)."""
    await _create_discussion(client, clerk_auth)
    r = await client.get("/api/v1/discussions", headers=clerk_auth("user_alice"))
    assert r.status_code == 200
    item = r.json()[0]
    assert set(item.keys()) == {"id", "title", "duration_seconds", "created_at"}


async def test_list_sorted_newest_first(client, clerk_auth):
    await _create_discussion(client, clerk_auth, title="First")
    await _create_discussion(client, clerk_auth, title="Second")
    r = await client.get("/api/v1/discussions", headers=clerk_auth("user_alice"))
    titles = [item["title"] for item in r.json()]
    assert titles == ["Second", "First"]


async def test_get_discussion_includes_full_transcript(client, clerk_auth):
    created = (await _create_discussion(client, clerk_auth)).json()
    r = await client.get(f"/api/v1/discussions/{created['id']}", headers=clerk_auth("user_alice"))
    assert r.status_code == 200
    assert r.json()["transcript"] == "We decided to use Postgres. Alice will review the schema."


async def test_rename_discussion(client, clerk_auth):
    created = (await _create_discussion(client, clerk_auth)).json()
    r = await client.put(
        f"/api/v1/discussions/{created['id']}", headers=clerk_auth("user_alice"), json={"title": "Renamed"}
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Renamed"
    # transcript/analysis are untouched by a rename
    assert r.json()["transcript"] == created["transcript"]


async def test_delete_discussion(client, clerk_auth):
    created = (await _create_discussion(client, clerk_auth)).json()
    r = await client.delete(f"/api/v1/discussions/{created['id']}", headers=clerk_auth("user_alice"))
    assert r.status_code == 204
    r2 = await client.get(f"/api/v1/discussions/{created['id']}", headers=clerk_auth("user_alice"))
    assert r2.status_code == 404


async def test_ownership_isolation(client, clerk_auth):
    created = (await _create_discussion(client, clerk_auth, user="user_alice")).json()

    r_list = await client.get("/api/v1/discussions", headers=clerk_auth("user_bob"))
    assert r_list.json() == []

    r_get = await client.get(f"/api/v1/discussions/{created['id']}", headers=clerk_auth("user_bob"))
    assert r_get.status_code == 404

    r_rename = await client.put(
        f"/api/v1/discussions/{created['id']}", headers=clerk_auth("user_bob"), json={"title": "Hijacked"}
    )
    assert r_rename.status_code == 404

    r_delete = await client.delete(f"/api/v1/discussions/{created['id']}", headers=clerk_auth("user_bob"))
    assert r_delete.status_code == 404


async def test_requires_auth(client):
    r = await client.get("/api/v1/discussions")
    assert r.status_code in (401, 403)


async def test_create_rejects_empty_transcript(client, clerk_auth):
    r = await _create_discussion(client, clerk_auth, transcript="")
    assert r.status_code == 422
