"""Unit tests for the connector framework's logging initializer."""

import logging

import pytest
from kamiwaza_sdk.connectors import server_kit as sk

pytestmark = pytest.mark.unit


def _reset_framework_logger():
    log = sk._LOG
    for handler in list(log.handlers):
        log.removeHandler(handler)
    log.setLevel(logging.NOTSET)
    log.propagate = True


def test_ensure_logging_sets_level_handler_and_propagate(monkeypatch):
    monkeypatch.delenv("KAMIWAZA_CONNECTOR_LOG_LEVEL", raising=False)
    _reset_framework_logger()
    sk._ensure_connector_logging()
    assert sk._LOG.level == logging.INFO
    assert sk._LOG.propagate is False
    assert len(sk._LOG.handlers) == 1


def test_ensure_logging_sets_level_even_with_preexisting_handler(monkeypatch):
    # Regression for PR #204 review (High #1): a pre-existing handler must not
    # short-circuit level config and leave the logger at the default WARNING,
    # which would silently drop the INFO op-outcome lines.
    monkeypatch.delenv("KAMIWAZA_CONNECTOR_LOG_LEVEL", raising=False)
    _reset_framework_logger()
    sk._LOG.addHandler(logging.NullHandler())
    sk._LOG.setLevel(logging.WARNING)
    sk._ensure_connector_logging()
    assert sk._LOG.level == logging.INFO  # level set despite the handler
    assert len(sk._LOG.handlers) == 1  # existing handler kept, not duplicated


def test_ensure_logging_is_idempotent(monkeypatch):
    monkeypatch.delenv("KAMIWAZA_CONNECTOR_LOG_LEVEL", raising=False)
    _reset_framework_logger()
    sk._ensure_connector_logging()
    sk._ensure_connector_logging()
    assert len(sk._LOG.handlers) == 1


def test_resolve_log_level_env_override(monkeypatch):
    monkeypatch.setenv("KAMIWAZA_CONNECTOR_LOG_LEVEL", "debug")
    assert sk._resolve_log_level() == logging.DEBUG


def test_resolve_log_level_bad_value_falls_back_to_info(monkeypatch):
    # A typo'd env value must not crash connector app construction.
    monkeypatch.setenv("KAMIWAZA_CONNECTOR_LOG_LEVEL", "NOT_A_LEVEL")
    assert sk._resolve_log_level() == logging.INFO
