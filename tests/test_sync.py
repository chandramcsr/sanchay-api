async def _signup(client, email="sync@example.com", password="hunter2222"):
    r = await client.post("/auth/signup", json={"email": email, "password": password, "display_name": "Sync Test"})
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_status_reports_no_backup_for_a_fresh_account(client):
    token = await _signup(client)
    r = await client.get("/sync/status", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"exists": False, "version": None, "updated_at": None}


async def test_pull_returns_404_when_nothing_pushed_yet(client):
    token = await _signup(client)
    r = await client.get("/sync/pull", headers=_auth(token))
    assert r.status_code == 404


async def test_first_push_creates_version_1(client):
    token = await _signup(client)
    r = await client.put("/sync/push", headers=_auth(token), json={
        "ciphertext": "opaque-bytes-1", "encryption_meta": "salt-and-iv-1", "based_on_version": 0,
    })
    assert r.status_code == 200
    assert r.json()["version"] == 1


async def test_push_then_pull_round_trips_the_ciphertext(client):
    token = await _signup(client)
    await client.put("/sync/push", headers=_auth(token), json={
        "ciphertext": "my-encrypted-ledger", "encryption_meta": "meta-123", "based_on_version": 0,
    })
    r = await client.get("/sync/pull", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["ciphertext"] == "my-encrypted-ledger"
    assert r.json()["encryption_meta"] == "meta-123"
    assert r.json()["version"] == 1


async def test_second_push_with_correct_version_succeeds_and_increments(client):
    token = await _signup(client)
    await client.put("/sync/push", headers=_auth(token), json={
        "ciphertext": "v1", "encryption_meta": "m", "based_on_version": 0,
    })
    r = await client.put("/sync/push", headers=_auth(token), json={
        "ciphertext": "v2", "encryption_meta": "m", "based_on_version": 1,
    })
    assert r.status_code == 200
    assert r.json()["version"] == 2


async def test_push_with_stale_version_is_rejected_with_409(client):
    """
    The core safety guarantee: a device that hasn't seen the latest
    version cannot silently overwrite it.
    """
    token = await _signup(client)
    await client.put("/sync/push", headers=_auth(token), json={
        "ciphertext": "v1", "encryption_meta": "m", "based_on_version": 0,
    })
    await client.put("/sync/push", headers=_auth(token), json={
        "ciphertext": "v2-from-device-a", "encryption_meta": "m", "based_on_version": 1,
    })
    # Device B still thinks version 1 is current and tries to push based on it.
    r = await client.put("/sync/push", headers=_auth(token), json={
        "ciphertext": "v2-from-device-b", "encryption_meta": "m", "based_on_version": 1,
    })
    assert r.status_code == 409

    # And critically: device A's write was NOT clobbered.
    pulled = await client.get("/sync/pull", headers=_auth(token))
    assert pulled.json()["ciphertext"] == "v2-from-device-a"


async def test_first_push_with_nonzero_based_on_version_is_rejected(client):
    token = await _signup(client)
    r = await client.put("/sync/push", headers=_auth(token), json={
        "ciphertext": "v1", "encryption_meta": "m", "based_on_version": 5,
    })
    assert r.status_code == 409


async def test_sync_endpoints_require_authentication(client):
    assert (await client.get("/sync/status")).status_code == 401
    assert (await client.get("/sync/pull")).status_code == 401
    assert (await client.put("/sync/push", json={"ciphertext": "x", "encryption_meta": "y", "based_on_version": 0})).status_code == 401


async def test_sync_data_is_isolated_between_users(client):
    """The one test that matters most: user B can never see user A's ciphertext."""
    tokenA = await _signup(client, email="userA-sync@example.com")
    tokenB = await _signup(client, email="userB-sync@example.com")

    await client.put("/sync/push", headers=_auth(tokenA), json={
        "ciphertext": "userA-secret-ledger", "encryption_meta": "m", "based_on_version": 0,
    })

    statusB = await client.get("/sync/status", headers=_auth(tokenB))
    assert statusB.json()["exists"] is False

    pullB = await client.get("/sync/pull", headers=_auth(tokenB))
    assert pullB.status_code == 404
