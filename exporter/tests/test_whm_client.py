import pytest

from whm_exporter.whm_client import WHMAPIError, WHMAuthError, WHMClient, WHMError


def test_call_sends_auth_and_api_version(fake_whm, client):
    data = client.call("version")
    assert data == {"version": "11.132.0.15"}
    function, params = fake_whm.requests[-1]
    assert function == "version"
    assert params["api.version"] == "1"


def test_call_passes_params(fake_whm, client):
    client.call("listaccts", {"want": "user,domain"})
    assert fake_whm.requests[-1][1]["want"] == "user,domain"


def test_wrong_token_raises_auth_error(fake_whm):
    bad = WHMClient(fake_whm.url, "root", "wrong", timeout=5)
    with pytest.raises(WHMAuthError, match="HTTP 403"):
        bad.call("version")


def test_result_zero_raises_api_error(client):
    with pytest.raises(WHMAPIError, match="Unknown app"):
        client.call("does_not_exist")


def test_permission_reason_raises_auth_error(fake_whm, client):
    fake_whm.overrides["fetch_ssl_vhosts"] = (
        200,
        {"metadata": {"result": 0, "reason": "Permission denied"}},
    )
    with pytest.raises(WHMAuthError, match="Permission denied"):
        client.call("fetch_ssl_vhosts")


def test_non_json_response(fake_whm, client):
    fake_whm.overrides["version"] = (200, "<html>cpsrvd</html>")
    with pytest.raises(WHMError, match="not JSON"):
        client.call("version")


def test_unexpected_status(fake_whm, client):
    fake_whm.overrides["version"] = (500, {"error": "boom"})
    with pytest.raises(WHMError, match="HTTP 500"):
        client.call("version")


def test_connection_refused():
    c = WHMClient("http://127.0.0.1:9", "root", "t", timeout=1)
    with pytest.raises(WHMError, match="request failed"):
        c.call("version")


def test_token_not_in_repr_or_errors(fake_whm):
    c = WHMClient(fake_whm.url, "root", "s3cr3t-token", timeout=5)
    with pytest.raises(WHMError) as excinfo:
        c.call("version")
    assert "s3cr3t-token" not in str(excinfo.value)
