import pytest

from whm_exporter.config import ALL_COLLECTORS, Config, ConfigError


def test_defaults():
    cfg = Config.from_env({"WHM_API_TOKEN": "abc"})
    assert cfg.whm_url == "https://127.0.0.1:2087"
    assert cfg.whm_user == "root"
    assert cfg.verify_tls is False
    assert cfg.listen_addr == "127.0.0.1"
    assert cfg.listen_port == 9877
    assert cfg.collectors == ALL_COLLECTORS
    assert cfg.intervals["services"] == 30


def test_token_required():
    with pytest.raises(ConfigError, match="token is missing"):
        Config.from_env({})


def test_token_file(tmp_path):
    f = tmp_path / "token"
    f.write_text("fromfile\n")
    cfg = Config.from_env({"WHM_API_TOKEN_FILE": str(f), "WHM_API_TOKEN": "ignored"})
    assert cfg.whm_token == "fromfile"


def test_token_hidden_from_repr():
    cfg = Config.from_env({"WHM_API_TOKEN": "supersecret"})
    assert "supersecret" not in repr(cfg)


def test_collectors_subset_and_unknown():
    cfg = Config.from_env({"WHM_API_TOKEN": "x", "COLLECTORS": "services, ssl,services"})
    assert cfg.collectors == ("services", "ssl")
    with pytest.raises(ConfigError, match="unknown collector"):
        Config.from_env({"WHM_API_TOKEN": "x", "COLLECTORS": "services,nope"})


def test_interval_override_and_validation():
    cfg = Config.from_env({"WHM_API_TOKEN": "x", "INTERVAL_SSL": "1800"})
    assert cfg.intervals["ssl"] == 1800
    with pytest.raises(ConfigError, match="INTERVAL_SSL"):
        Config.from_env({"WHM_API_TOKEN": "x", "INTERVAL_SSL": "1"})
    with pytest.raises(ConfigError, match="integer"):
        Config.from_env({"WHM_API_TOKEN": "x", "INTERVAL_SSL": "soon"})


@pytest.mark.parametrize("raw,expected", [("true", True), ("1", True), ("no", False)])
def test_bool_parsing(raw, expected):
    assert Config.from_env({"WHM_API_TOKEN": "x", "WHM_VERIFY_TLS": raw}).verify_tls is expected


def test_bad_bool_and_url():
    with pytest.raises(ConfigError, match="WHM_VERIFY_TLS"):
        Config.from_env({"WHM_API_TOKEN": "x", "WHM_VERIFY_TLS": "maybe"})
    with pytest.raises(ConfigError, match="WHM_URL"):
        Config.from_env({"WHM_API_TOKEN": "x", "WHM_URL": "127.0.0.1:2087"})
