import sentry_sdk


def test_sentry_has_no_transport_when_dsn_is_unset():
    """
    Guards the exact behavior config.py's docstring promises: with no
    SENTRY_DSN (the default in every local/test environment, per
    conftest.py), sentry_sdk.init() must be a genuine no-op -- no
    transport configured, nothing capture_exception() could send
    anywhere -- not a silent attempt to reach a real Sentry project.
    A future sentry-sdk upgrade changing this default behavior should
    fail this test, not go unnoticed.
    """
    client = sentry_sdk.get_client()
    assert client.dsn is None
    assert client.transport is None


def test_capture_exception_does_not_raise_with_no_transport_configured():
    try:
        raise ValueError("no DSN configured, should be swallowed safely")
    except ValueError as e:
        sentry_sdk.capture_exception(e)  # must not raise
