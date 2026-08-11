"""Guard the Celery/Redis TLS configuration.

Regression test for a production crash: with a TLS Redis URL (Upstash uses
rediss://), Celery's *result backend* raises at startup unless ssl_cert_reqs
is supplied:

    ValueError: A rediss:// URL must have parameter ssl_cert_reqs and this
    must be set to CERT_REQUIRED, CERT_OPTIONAL, or CERT_NONE

The broker imposes no such requirement, so only the worker crash-looped while
the API stayed up. Because no task result is ever read in this codebase, the
fix is to disable the result backend entirely rather than to append an
ssl_cert_reqs query parameter to the URL.
"""

import importlib
import ssl

import pytest


def _reload_celery(monkeypatch, url):
    """Re-import celery_app with a given REDIS_URL."""
    import app.config

    monkeypatch.setattr(app.config.settings, "REDIS_URL", url, raising=False)
    import app.tasks.celery_app as mod

    return importlib.reload(mod)


@pytest.fixture(autouse=True)
def _restore():
    """Leave the module in its normal state for other tests."""
    yield
    import app.tasks.celery_app as mod

    importlib.reload(mod)


def test_tls_url_does_not_raise_and_disables_result_backend(monkeypatch):
    """A rediss:// URL must import cleanly and use no result backend."""
    mod = _reload_celery(monkeypatch, "rediss://default:pw@example.upstash.io:6379")

    # The exact call that crashed the worker (celery/apps/worker.py emit_banner).
    assert mod.celery_app.backend.as_uri() == "disabled://"
    assert type(mod.celery_app.backend).__name__ == "DisabledBackend"


def test_tls_url_verifies_certificates(monkeypatch):
    """TLS connections must verify the server certificate, not silently skip it."""
    mod = _reload_celery(monkeypatch, "rediss://default:pw@example.upstash.io:6379")

    assert mod.celery_app.conf.broker_use_ssl == {"ssl_cert_reqs": ssl.CERT_REQUIRED}


def test_plain_url_sets_no_ssl_options(monkeypatch):
    """A non-TLS redis:// URL must not get SSL options attached."""
    mod = _reload_celery(monkeypatch, "redis://localhost:6379/0")

    assert mod.celery_app.conf.broker_use_ssl is None
    assert mod.celery_app.backend.as_uri() == "disabled://"


def test_results_are_ignored(monkeypatch):
    """Disabling the backend is only safe while results are never read."""
    mod = _reload_celery(monkeypatch, "redis://localhost:6379/0")

    assert mod.celery_app.conf.task_ignore_result is True
