"""
Tests for settings.async_database_url's translation logic — written
immediately after it broke a real deploy (switching the database
host to Neon, whose connection strings carry libpq-style
?sslmode=require that asyncpg rejects outright). Each case here is a
URL shape a real hosting provider actually hands out.
"""

from app.core.config import Settings


def _url_for(database_url: str) -> str:
    return Settings(database_url=database_url, jwt_secret_key="test-not-real").async_database_url


def test_neon_style_url_with_sslmode_require_is_translated_for_asyncpg():
    # The exact shape that broke the deploy: Neon's pooled connection
    # string. asyncpg crashes on sslmode; it must become ssl=.
    url = _url_for("postgresql://user:pass@ep-cool-name-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require")
    assert url.startswith("postgresql+asyncpg://")
    assert "sslmode" not in url  # the thing that actually crashed
    assert "ssl=require" in url


def test_verify_full_maps_to_require_not_dropped():
    # verify-ca / verify-full must NOT be silently dropped (that would
    # downgrade a security setting) — they map to asyncpg's require,
    # which verifies against the system CA bundle by default.
    url = _url_for("postgresql://u:p@host/db?sslmode=verify-full")
    assert "ssl=require" in url
    assert "sslmode" not in url


def test_sslmode_disable_becomes_ssl_disable():
    url = _url_for("postgresql://u:p@host/db?sslmode=disable")
    assert "ssl=disable" in url
    assert "sslmode" not in url


def test_legacy_postgres_scheme_with_sslmode_gets_both_translations():
    # Heroku/older-Render style: legacy scheme AND an sslmode param.
    url = _url_for("postgres://u:p@host/db?sslmode=require")
    assert url.startswith("postgresql+asyncpg://")
    assert "ssl=require" in url


def test_plain_render_url_without_sslmode_is_unchanged_beyond_the_scheme():
    # The shape that's been in production all along — must keep
    # working exactly as before, no params invented.
    url = _url_for("postgresql://u:p@dpg-something.render.com/sanchay")
    assert url == "postgresql+asyncpg://u:p@dpg-something.render.com/sanchay"
    assert "ssl" not in url


def test_sqlite_urls_are_untouched_by_the_ssl_logic():
    assert _url_for("sqlite:///./dev.db") == "sqlite+aiosqlite:///./dev.db"


def test_other_query_params_survive_the_translation():
    url = _url_for("postgresql://u:p@host/db?sslmode=require&application_name=sanchay")
    assert "ssl=require" in url
    assert "application_name=sanchay" in url  # neighbors untouched
