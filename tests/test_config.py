"""Smoke tests for the package skeleton and config loading."""

from __future__ import annotations

import pytest

from mlbreview.config import Config


def test_package_imports_cleanly() -> None:
    import mlbreview  # noqa: F401
    import mlbreview.config  # noqa: F401
    import mlbreview.data  # noqa: F401
    import mlbreview.scoring  # noqa: F401
    import mlbreview.render  # noqa: F401


def test_config_load_dry_run_tolerates_missing_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("DIGEST_TO_EMAIL", raising=False)
    monkeypatch.delenv("DIGEST_FROM_EMAIL", raising=False)

    cfg = Config.load(require_secrets=False)

    assert cfg.anthropic_api_key is None
    assert cfg.resend_api_key is None
    assert cfg.digest_to_email is None
    assert cfg.digest_from_email == "MLB Digest <onboarding@resend.dev>"


def test_config_load_production_requires_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("DIGEST_TO_EMAIL", raising=False)

    with pytest.raises(EnvironmentError) as exc_info:
        Config.load(require_secrets=True)

    msg = str(exc_info.value)
    assert "ANTHROPIC_API_KEY" in msg
    assert "RESEND_API_KEY" in msg
    assert "DIGEST_TO_EMAIL" in msg


def test_config_load_production_succeeds_with_all_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("DIGEST_TO_EMAIL", "you@example.com")

    cfg = Config.load(require_secrets=True)

    assert cfg.anthropic_api_key == "sk-ant-test"
    assert cfg.resend_api_key == "re_test"
    assert cfg.digest_to_email == "you@example.com"
