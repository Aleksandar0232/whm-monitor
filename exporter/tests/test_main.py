"""End-to-end: the real entry point against the fake WHM server."""

import pytest
from conftest import TOKEN

from whm_exporter.__main__ import main


@pytest.fixture
def env(monkeypatch, fake_whm, tmp_path):
    spool = tmp_path / "input"
    spool.mkdir()
    (spool / "1aAAAA-000001-AA-H").write_text("1aAAAA-000001-AA-H\nalpha 1 1\n<a@b.c>\n1 0\n")
    monkeypatch.setenv("WHM_URL", fake_whm.url)
    monkeypatch.setenv("WHM_API_TOKEN", TOKEN)
    monkeypatch.setenv("EXIM_SPOOL_DIR", str(spool))
    return monkeypatch


def test_check_all_ok(env, capsys):
    assert main(["--check"]) == 0
    out = capsys.readouterr().out
    for name in ("server", "services", "accounts", "disk", "ssl", "cphulk", "email", "exim_queue"):
        assert f"{name}" in out
    assert "FAIL" not in out


def test_check_reports_failure(env, fake_whm, capsys):
    fake_whm.overrides["servicestatus"] = (403, "denied")
    assert main(["--check"]) == 1
    out = capsys.readouterr().out
    assert "services     FAIL" in out
    assert "HTTP 403" in out


def test_once_prints_metrics(env, capsys):
    env.setenv("COLLECTORS", "services,exim_queue")
    assert main(["--once"]) == 0
    out = capsys.readouterr().out
    assert 'whm_service_running{service="mysql"} 0.0' in out
    assert "whm_exim_queue_messages 1.0" in out
    assert 'whm_collector_up{collector="services"} 1.0' in out
    assert "whm_exporter_build_info" in out
    assert "whm_account_info" not in out  # collector not enabled


def test_config_error_exit_code(monkeypatch, capsys):
    monkeypatch.delenv("WHM_API_TOKEN", raising=False)
    monkeypatch.delenv("WHM_API_TOKEN_FILE", raising=False)
    assert main(["--check"]) == 2
    assert "token is missing" in capsys.readouterr().err
