"""
Rate limiting was added to every shared-expenses endpoint except the
2 (invite preview/accept) that already had it -- verified via
GET /openapi.json-adjacent boot check that all 22 got decorated, but
that only proves the decorator is present, not that it actually
enforces. These tests hit real limits against real endpoints to prove
the mechanism works, not just that the annotation exists.

Not exhaustive (22 endpoints, 22 tests would be pure repetition of the
same slowapi mechanism) -- one mutating endpoint and one read endpoint,
representative of the two tiers of limit used.
"""

from tests.test_shared_expenses_router import _auth, _signup


async def test_create_group_is_rate_limited(client):
    token, _ = await _signup(client, "ratelimit-creategroup@example.com", "Tester")
    for i in range(30):
        await client.post("/api/v1/shared-expenses/groups", headers=_auth(token), json={"name": f"Group {i}", "members": []})
    r = await client.post("/api/v1/shared-expenses/groups", headers=_auth(token), json={"name": "One too many", "members": []})
    assert r.status_code == 429


async def test_my_balances_is_rate_limited(client):
    token, _ = await _signup(client, "ratelimit-balances@example.com", "Tester")
    for _ in range(120):
        await client.get("/api/v1/shared-expenses/balances", headers=_auth(token))
    r = await client.get("/api/v1/shared-expenses/balances", headers=_auth(token))
    assert r.status_code == 429
