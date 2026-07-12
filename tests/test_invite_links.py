"""
Tests for the invite-link feature: a shareable link (built around a
PendingGroupInvite's id, an unguessable UUID) that lets someone join a
group without needing to sign up with the exact email address the
inviter typed. Two endpoints:

- GET /invites/{id} — public, unauthenticated preview
- POST /invites/{id}/accept — authenticated join

Deliberately tests the trust model explicitly: possession of the link
is the authorization, not an email match (same as Slack/Discord invite
links) — a genuine widening from the email-matching join_pending_invites
path, not an oversight.
"""

import pytest

from app.core.config import settings
from app.models.pending_group_invite import PendingGroupInvite
from tests.conftest import get_one
from tests.test_shared_expenses_router import _auth, _signup


@pytest.fixture
def enable_invite_links(monkeypatch):
    """
    The feature defaults OFF (settings.invite_links_enabled = False) —
    built and tested, held back until the frontend UI around it ships
    alongside it. Every test below that exercises the actual feature
    requests this fixture explicitly; the two default-state tests at
    the bottom deliberately do NOT, to prove the off-by-default
    behavior independently of the feature's own correctness.
    """
    monkeypatch.setattr(settings, "invite_links_enabled", True)


async def _create_group_with_invite(client, inviter_email="inviter@example.com", invite_email="invitee@example.com", invite_name="Sam"):
    token, _ = await _signup(client, inviter_email, "Inviter")
    r = await client.post(
        "/api/v1/shared-expenses/groups", headers=_auth(token),
        json={"name": "Roommates", "members": [{"email": invite_email, "name": invite_name}]},
    )
    invite_id = r.json()["pending_invites"][0]["id"]
    return token, r.json()["id"], invite_id


# --- preview (public, unauthenticated) --------------------------------


async def test_preview_returns_group_and_inviter_name_with_no_auth(enable_invite_links, client):
    _, _, invite_id = await _create_group_with_invite(client, "preview-inviter1@example.com", "preview-invitee1@example.com")
    r = await client.get(f"/api/v1/shared-expenses/invites/{invite_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["group_name"] == "Roommates"
    assert body["inviter_name"] == "Inviter"


async def test_preview_requires_no_authorization_header_at_all(enable_invite_links, client):
    _, _, invite_id = await _create_group_with_invite(client, "noauth-inviter@example.com", "noauth-invitee@example.com")
    r = await client.get(f"/api/v1/shared-expenses/invites/{invite_id}")  # no Authorization header
    assert r.status_code == 200


async def test_preview_404s_for_a_made_up_id(enable_invite_links, client):
    r = await client.get("/api/v1/shared-expenses/invites/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


async def test_preview_only_exposes_group_name_inviter_and_invitee_name(enable_invite_links, client):
    """Nothing financial, nothing membership-related — that's the whole safety argument for this being public."""
    _, _, invite_id = await _create_group_with_invite(client, "minimal-inviter@example.com", "minimal-invitee@example.com")
    r = await client.get(f"/api/v1/shared-expenses/invites/{invite_id}")
    assert set(r.json().keys()) == {"group_name", "inviter_name", "invitee_name"}


async def test_preview_returns_the_invitee_name_for_signup_prefill(enable_invite_links, client):
    _, _, invite_id = await _create_group_with_invite(client, "prefill-inviter@example.com", "prefill-invitee@example.com", invite_name="Sam Roommate")
    r = await client.get(f"/api/v1/shared-expenses/invites/{invite_id}")
    assert r.json()["invitee_name"] == "Sam Roommate"


# --- accept (authenticated) -------------------------------------------


async def test_accept_adds_the_authenticated_user_to_the_group(enable_invite_links, client):
    _, group_id, invite_id = await _create_group_with_invite(client, "accept-inviter1@example.com", "accept-invitee1@example.com")
    joiner_token, joiner_id = await _signup(client, "joiner1@example.com", "Joiner")

    r = await client.post(f"/api/v1/shared-expenses/invites/{invite_id}/accept", headers=_auth(joiner_token))
    assert r.status_code == 200
    member_ids = [m["user_id"] for m in r.json()["members"]]
    assert joiner_id in member_ids


async def test_accept_works_even_when_joiner_email_does_not_match_the_invited_email(enable_invite_links, client):
    """
    The core trust-model test: the invite was addressed to
    'accept-invitee2@example.com', but a DIFFERENT account accepts it.
    This must succeed -- possession of the link is the authorization,
    not an email match. This is what makes invite links actually solve
    the typo'd-email / wrong-inbox problem email-matching alone can't.
    """
    _, group_id, invite_id = await _create_group_with_invite(client, "accept-inviter2@example.com", "accept-invitee2@example.com")
    joiner_token, joiner_id = await _signup(client, "totally-different-email@example.com", "Different Person")

    r = await client.post(f"/api/v1/shared-expenses/invites/{invite_id}/accept", headers=_auth(joiner_token))
    assert r.status_code == 200
    member_ids = [m["user_id"] for m in r.json()["members"]]
    assert joiner_id in member_ids


async def test_accept_requires_authentication(enable_invite_links, client):
    _, _, invite_id = await _create_group_with_invite(client, "noauth2-inviter@example.com", "noauth2-invitee@example.com")
    r = await client.post(f"/api/v1/shared-expenses/invites/{invite_id}/accept")  # no token
    assert r.status_code == 401


async def test_accept_consumes_the_invite_so_it_cannot_be_used_twice(enable_invite_links, client):
    _, group_id, invite_id = await _create_group_with_invite(client, "onceonly-inviter@example.com", "onceonly-invitee@example.com")
    first_joiner, _ = await _signup(client, "first-joiner@example.com", "First")
    await client.post(f"/api/v1/shared-expenses/invites/{invite_id}/accept", headers=_auth(first_joiner))

    second_joiner, _ = await _signup(client, "second-joiner@example.com", "Second")
    r = await client.post(f"/api/v1/shared-expenses/invites/{invite_id}/accept", headers=_auth(second_joiner))
    assert r.status_code == 404


async def test_accept_removes_the_pending_invite_row(enable_invite_links, client, db_session):
    _, group_id, invite_id = await _create_group_with_invite(client, "rowcheck-inviter@example.com", "rowcheck-invitee@example.com")
    joiner_token, _ = await _signup(client, "rowcheck-joiner@example.com", "Joiner")
    await client.post(f"/api/v1/shared-expenses/invites/{invite_id}/accept", headers=_auth(joiner_token))

    remaining = await db_session.get(PendingGroupInvite, invite_id)
    assert remaining is None


async def test_accept_is_a_no_op_if_the_link_holder_is_already_a_member(enable_invite_links, client):
    """Inviter accepting their own group's invite link (e.g. testing their own share link) shouldn't error or duplicate them."""
    inviter_token, group_id, invite_id = await _create_group_with_invite(client, "selfaccept-inviter@example.com", "selfaccept-invitee@example.com")
    r = await client.post(f"/api/v1/shared-expenses/invites/{invite_id}/accept", headers=_auth(inviter_token))
    assert r.status_code == 200
    member_ids = [m["user_id"] for m in r.json()["members"]]
    assert len(member_ids) == len(set(member_ids))  # no duplicate membership rows


async def test_accept_is_rate_limited(enable_invite_links, client):
    _, _, invite_id = await _create_group_with_invite(client, "ratelimit-inviter@example.com", "ratelimit-invitee@example.com")
    token, _ = await _signup(client, "ratelimit-joiner@example.com", "Joiner")
    for _ in range(30):
        await client.post(f"/api/v1/shared-expenses/invites/{invite_id}/accept", headers=_auth(token))
    r = await client.post(f"/api/v1/shared-expenses/invites/{invite_id}/accept", headers=_auth(token))
    assert r.status_code == 429


# --- feature switch: off by default ------------------------------------
#
# These two deliberately do NOT request enable_invite_links, to prove
# the default state independently of the feature's own correctness
# (tested everywhere above). The invite itself is real and valid --
# these are testing that the SWITCH, not the invite, is what blocks
# access.


async def test_preview_404s_by_default_when_feature_is_disabled(client):
    _, _, invite_id = await _create_group_with_invite(client, "flagoff-preview-inviter@example.com", "flagoff-preview-invitee@example.com")
    r = await client.get(f"/api/v1/shared-expenses/invites/{invite_id}")
    assert r.status_code == 404


async def test_accept_404s_by_default_when_feature_is_disabled_even_with_valid_auth(client):
    _, _, invite_id = await _create_group_with_invite(client, "flagoff-accept-inviter@example.com", "flagoff-accept-invitee@example.com")
    token, _ = await _signup(client, "flagoff-joiner@example.com", "Joiner")
    r = await client.post(f"/api/v1/shared-expenses/invites/{invite_id}/accept", headers=_auth(token))
    assert r.status_code == 404
