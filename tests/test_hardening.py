async def test_password_redacted_when_another_field_fails_validation(client):
    """
    The bug this guards against: a missing display_name causes a 422
    whose 'input' field historically echoed the ENTIRE request body,
    including the plaintext password.
    """
    r = await client.post("/auth/signup", json={"email": "redact@example.com", "password": "hunter2222"})
    assert r.status_code == 422
    assert "hunter2222" not in r.text
    assert "[redacted]" in r.text


async def test_password_redacted_when_password_itself_is_invalid(client):
    r = await client.post("/auth/signup", json={"email": "weakpw@example.com", "password": "short", "display_name": "X"})
    assert r.status_code == 422
    assert "short" not in r.text


async def test_login_is_rate_limited(client):
    await client.post("/auth/signup", json={"email": "ratelimit@example.com", "password": "hunter2222", "display_name": "RL"})
    # Login allows 5/minute — the first 5 (even with a wrong password,
    # since rate limiting must apply before credentials are checked)
    # succeed as requests; the 6th is throttled.
    for _ in range(5):
        r = await client.post("/auth/login", json={"email": "ratelimit@example.com", "password": "wrongpass1"})
        assert r.status_code == 401
    r = await client.post("/auth/login", json={"email": "ratelimit@example.com", "password": "wrongpass1"})
    assert r.status_code == 429


async def test_signup_is_rate_limited(client):
    # Signup allows 10/minute.
    for i in range(10):
        await client.post("/auth/signup", json={"email": f"burst{i}@example.com", "password": "hunter2222", "display_name": "X"})
    r = await client.post("/auth/signup", json={"email": "burst-overflow@example.com", "password": "hunter2222", "display_name": "X"})
    assert r.status_code == 429
