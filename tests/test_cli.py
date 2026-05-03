"""Smoke tests for the CLI entrypoint."""

from __future__ import annotations

import pytest

from mlbreview.__main__ import main


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "mlbreview" in out
    assert "--dry-run" in out


def test_dry_run_exits_zero_without_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("DIGEST_TO_EMAIL", raising=False)

    assert main(["--dry-run"]) == 0


def test_production_run_errors_when_secrets_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("DIGEST_TO_EMAIL", raising=False)

    with pytest.raises(EnvironmentError):
        main([])
