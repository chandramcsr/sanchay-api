async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}


async def test_root(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "sanchay-api"


async def test_signup_creates_user_and_returns_token(client):
    r = await client.post("/api/v1/auth/signup", json={"email": "chandra@example.com", "password": "hunter22", "display_name": "Chandra"})
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["email"] == "chandra@example.com"
    assert "access_token" in body and body["token_type"] == "bearer"


async def test_signup_normalizes_email_case(client):
    await client.post("/api/v1/auth/signup", json={"email": "Chandra@Example.com", "password": "hunter22", "display_name": "Chandra"})
    r = await client.post("/api/v1/auth/login", json={"email": "chandra@example.com", "password": "hunter22"})
    assert r.status_code == 200


async def test_signup_rejects_duplicate_email(client):
    await client.post("/api/v1/auth/signup", json={"email": "dup@example.com", "password": "hunter22", "display_name": "Dup"})
    r = await client.post("/api/v1/auth/signup", json={"email": "dup@example.com", "password": "different99", "display_name": "Dup2"})
    assert r.status_code == 400


async def test_signup_rejects_weak_password(client):
    r = await client.post("/api/v1/auth/signup", json={"email": "weak@example.com", "password": "short", "display_name": "Weak"})
    assert r.status_code == 422
    r2 = await client.post("/api/v1/auth/signup", json={"email": "weak2@example.com", "password": "nodigitshere", "display_name": "Weak"})
    assert r2.status_code == 422


async def test_signup_rejects_missing_display_name(client):
    r = await client.post("/api/v1/auth/signup", json={"email": "noname@example.com", "password": "hunter22"})
    assert r.status_code == 422


async def test_signup_rejects_blank_display_name(client):
    r = await client.post("/api/v1/auth/signup", json={"email": "blank@example.com", "password": "hunter22", "display_name": "   "})
    assert r.status_code == 422


async def test_signup_trims_display_name(client):
    r = await client.post("/api/v1/auth/signup", json={"email": "trim@example.com", "password": "hunter22", "display_name": "  Chandra  "})
    assert r.json()["user"]["display_name"] == "Chandra"


async def test_signup_rejects_invalid_email(client):
    r = await client.post("/api/v1/auth/signup", json={"email": "not-an-email", "password": "hunter22", "display_name": "X"})
    assert r.status_code == 422


async def test_login_succeeds_with_correct_credentials(client):
    await client.post("/api/v1/auth/signup", json={"email": "login@example.com", "password": "hunter22", "display_name": "Login"})
    r = await client.post("/api/v1/auth/login", json={"email": "login@example.com", "password": "hunter22"})
    assert r.status_code == 200
    assert "access_token" in r.json()


async def test_login_fails_with_wrong_password(client):
    await client.post("/api/v1/auth/signup", json={"email": "login2@example.com", "password": "hunter22", "display_name": "Login2"})
    r = await client.post("/api/v1/auth/login", json={"email": "login2@example.com", "password": "wrongpass1"})
    assert r.status_code == 401


async def test_login_fails_for_nonexistent_user(client):
    r = await client.post("/api/v1/auth/login", json={"email": "ghost@example.com", "password": "hunter22"})
    assert r.status_code == 401


async def test_login_and_signup_errors_are_identically_worded(client):
    """
    Prevents account enumeration: whether the email exists or the
    password is wrong, the caller sees the same message either way.
    """
    await client.post("/api/v1/auth/signup", json={"email": "real@example.com", "password": "hunter22", "display_name": "Real"})
    wrong_pw = await client.post("/api/v1/auth/login", json={"email": "real@example.com", "password": "wrongpass1"})
    no_user = await client.post("/api/v1/auth/login", json={"email": "nouser@example.com", "password": "hunter22"})
    assert wrong_pw.json()["detail"] == no_user.json()["detail"]


async def test_me_requires_a_token(client):
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401


async def test_me_rejects_garbage_token(client):
    r = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


async def test_me_returns_current_user_with_valid_token(client):
    signup = await client.post("/api/v1/auth/signup", json={"email": "me@example.com", "password": "hunter22", "display_name": "Chandra"})
    token = signup.json()["access_token"]
    r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "me@example.com"
    assert r.json()["display_name"] == "Chandra"


async def test_password_is_never_returned_in_any_response(client):
    signup = await client.post("/api/v1/auth/signup", json={"email": "secret@example.com", "password": "hunter22", "display_name": "Secret"})
    assert "password" not in signup.text
    assert "hashed_password" not in signup.text
    token = signup.json()["access_token"]
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert "password" not in me.text
