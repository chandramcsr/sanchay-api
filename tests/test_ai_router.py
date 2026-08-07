async def _create_account(client, clerk_auth, user="user_alice"):
    r = await client.post(
        "/api/v1/accounts",
        headers=clerk_auth(user),
        json={"name": "Checking", "type": "checking", "starting_balance": 1000},
    )
    return r.json()["id"]


async def _create_transaction(client, clerk_auth, account_id, *, user="user_alice", **kwargs):
    payload = {"account_id": account_id, "amount": -25.0, "description": "Groceries", "date": "2026-08-01"}
    payload.update(kwargs)
    r = await client.post("/api/v1/transactions", headers=clerk_auth(user), json=payload)
    return r.json()


async def test_transaction_gets_embedded_on_create(client, clerk_auth):
    """The core wiring this whole feature depends on: creating a
    transaction should populate its embedding, not leave it null."""
    account_id = await _create_account(client, clerk_auth)
    body = await _create_transaction(client, clerk_auth, account_id, description="Whole Foods run")
    assert body["amount"] == -25.0
    # embedding isn't in TransactionOut (internal, not API surface) --
    # verify indirectly: a question naming this transaction should
    # find it via /ai/ask.
    r = await client.post(
        "/api/v1/ai/ask", headers=clerk_auth("user_alice"), json={"question": "Whole Foods groceries"}
    )
    assert r.status_code == 200
    assert r.json()["abstained"] is False
    assert len(r.json()["sources"]) >= 1


async def test_update_re_embeds_transaction(client, clerk_auth):
    """Editing a transaction's description should update what it's
    findable by -- a stale embedding would mean the edit silently
    doesn't take effect for retrieval, even though the transaction
    itself displays correctly."""
    account_id = await _create_account(client, clerk_auth)
    body = await _create_transaction(client, clerk_auth, account_id, description="Original description xyzzy")
    transaction_id = body["id"]

    await client.put(
        f"/api/v1/transactions/{transaction_id}",
        headers=clerk_auth("user_alice"),
        json={"description": "Renamed to plugh unique word"},
    )

    r = await client.post(
        "/api/v1/ai/ask", headers=clerk_auth("user_alice"), json={"question": "plugh unique word"}
    )
    assert r.json()["abstained"] is False
    found_texts = [s["text"] for s in r.json()["sources"]]
    assert any("Renamed to plugh" in t for t in found_texts)


async def test_ask_abstains_when_no_relevant_transactions(client, clerk_auth):
    """No transactions at all -- must abstain, not hallucinate an
    answer or error."""
    r = await client.post(
        "/api/v1/ai/ask", headers=clerk_auth("user_alice"), json={"question": "how much did I spend on rent"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["abstained"] is True
    assert body["sources"] == []


async def test_ask_only_retrieves_the_caller_own_transactions(client, clerk_auth):
    """The non-negotiable one: user_bob's transactions must never
    surface in user_alice's answer, however similar the question."""
    alice_account = await _create_account(client, clerk_auth, user="user_alice")
    bob_account = await _create_account(client, clerk_auth, user="user_bob")

    await _create_transaction(
        client, clerk_auth, alice_account, user="user_alice", description="Alice private grocery trip"
    )
    await _create_transaction(
        client, clerk_auth, bob_account, user="user_bob", description="Bob private grocery trip"
    )

    r = await client.post(
        "/api/v1/ai/ask", headers=clerk_auth("user_alice"), json={"question": "grocery trip"}
    )
    sources_text = " ".join(s["text"] for s in r.json()["sources"])
    assert "Bob" not in sources_text


async def test_ask_ranks_relevant_transaction_above_irrelevant_one(client, clerk_auth):
    """Verifies retrieval actually discriminates -- not just that it
    returns *something*. Uses the deterministic stub embedding
    (conftest.py's hashing-trick bag-of-words), which genuinely scores
    word-overlap higher, so this is testing real ranking behavior."""
    account_id = await _create_account(client, clerk_auth)
    await _create_transaction(
        client, clerk_auth, account_id, description="zephyr quartz falcon unrelated thing"
    )
    await _create_transaction(
        client, clerk_auth, account_id, description="mortgage payment mortgage housing mortgage"
    )

    r = await client.post(
        "/api/v1/ai/ask", headers=clerk_auth("user_alice"), json={"question": "mortgage payment housing"}
    )
    sources = r.json()["sources"]
    assert len(sources) >= 1
    assert "mortgage" in sources[0]["text"].lower()


async def test_ask_flags_ungrounded_when_citation_invalid(client, clerk_auth, monkeypatch):
    """If the model cites a passage it wasn't given, grounded must be
    False -- the deterministic citation-verification check, not a
    judgment call. Overrides the stub generate_fn for this one test to
    force an invalid citation."""
    from app.main import app
    from app.routers.ai import get_generate_fn

    def bad_generate_fn(system, user):
        return "According to the records [P99], you spent a lot."  # P99 was never supplied

    account_id = await _create_account(client, clerk_auth)
    await _create_transaction(client, clerk_auth, account_id, description="Coffee shop visit")

    app.dependency_overrides[get_generate_fn] = lambda: bad_generate_fn
    try:
        r = await client.post(
            "/api/v1/ai/ask", headers=clerk_auth("user_alice"), json={"question": "coffee shop"}
        )
    finally:
        # Restore the normal stub so later tests in this session aren't affected.
        from tests.conftest import stub_generate_fn

        app.dependency_overrides[get_generate_fn] = lambda: stub_generate_fn

    assert r.json()["grounded"] is False


async def test_ask_requires_auth(client):
    r = await client.post("/api/v1/ai/ask", json={"question": "test"})
    assert r.status_code in (401, 403)


async def test_ask_validates_empty_question(client, clerk_auth):
    r = await client.post("/api/v1/ai/ask", headers=clerk_auth("user_alice"), json={"question": ""})
    assert r.status_code == 422
