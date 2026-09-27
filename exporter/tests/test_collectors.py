import pytest
from conftest import value

from whm_exporter.collectors.accounts import AccountsCollector
from whm_exporter.collectors.cphulk import CPHulkCollector
from whm_exporter.collectors.disk import DiskCollector
from whm_exporter.collectors.email import EmailCollector
from whm_exporter.collectors.server import ServerCollector
from whm_exporter.collectors.services import ServicesCollector
from whm_exporter.collectors.ssl import SSLCollector
from whm_exporter.whm_client import WHMAPIError

NOW = 1790000000


def test_server(client):
    fams = ServerCollector(client).collect()
    assert value(fams, "whm_server_info", version="11.132.0.15") == 1


def test_services(client):
    fams = ServicesCollector(client).collect()
    assert value(fams, "whm_service_running", service="httpd") == 1
    assert value(fams, "whm_service_running", service="mysql") == 0
    assert value(fams, "whm_service_enabled", service="named") == 0
    # not monitored -> WHM omits "running" -> we must not invent a 0
    assert value(fams, "whm_service_running", service="named") is None
    assert value(fams, "whm_service_monitored", service="named") == 0


def test_accounts(fake_whm, client):
    fams = AccountsCollector(client).collect()
    assert fake_whm.requests[-1][1]["want"] == "user,domain,suspended,plan,owner"
    assert value(fams, "whm_accounts", state="active") == 2
    assert value(fams, "whm_accounts", state="suspended") == 1
    assert value(fams, "whm_account_suspended", user="beta") == 1
    assert value(fams, "whm_account_suspended", user="gamma") == 0
    assert (
        value(
            fams,
            "whm_account_info",
            user="gamma",
            domain="gamma.example",
            plan="default",
            owner="root",
        )
        == 1
    )


def test_disk(fake_whm, client):
    fams = DiskCollector(client).collect()
    assert fake_whm.requests[-1][1]["cache_mode"] == "on"
    assert value(fams, "whm_account_disk_used_bytes", user="alpha") == 1048576 * 1024
    assert value(fams, "whm_account_disk_limit_bytes", user="alpha") == 2097152 * 1024
    assert value(fams, "whm_account_disk_used_bytes", user="beta") == 512 * 1024
    # null limit = unlimited = no series
    assert value(fams, "whm_account_disk_limit_bytes", user="beta") is None
    assert value(fams, "whm_account_inodes_limit", user="alpha") is None
    assert value(fams, "whm_account_inodes_limit", user="gamma") == 100000


def test_ssl(client):
    fams = SSLCollector(client).collect()
    # duplicate servername keeps the earliest expiry
    assert (
        value(
            fams,
            "whm_ssl_cert_not_after_timestamp_seconds",
            servername="alpha.example",
            user="alpha",
        )
        == NOW + 60 * 86400
    )
    assert (
        value(fams, "whm_ssl_cert_self_signed", servername="old.gamma.example", user="gamma") == 1
    )
    assert (
        value(
            fams,
            "whm_ssl_cert_info",
            servername="beta.example",
            user="beta",
            issuer="Sectigo Limited",
            validation_type="dv",
            vhost_type="main",
        )
        == 1
    )
    # vhost without certificate data is skipped
    names = {s.labels["servername"] for f in fams for s in f.samples}
    assert "nocert.example" not in names


def test_cphulk_counts_unique_ips(client):
    fams = CPHulkCollector(client).collect()
    assert value(fams, "whm_cphulk_blocked_ips", list="brute") == 2
    assert value(fams, "whm_cphulk_blocked_ips", list="excessive") == 1


def test_cphulk_disabled_fails(fake_whm, client):
    fake_whm.overrides["get_cphulk_brutes"] = (
        200,
        {"metadata": {"result": 0, "reason": "cPHulk is disabled"}},
    )
    with pytest.raises(WHMAPIError, match="disabled"):
        CPHulkCollector(client).collect()


def test_email_aggregates_per_user(fake_whm, client):
    fams = EmailCollector(client, window=3600, clock=lambda: NOW).collect()
    params = fake_whm.requests[-1][1]
    assert params["starttime"] == str(NOW - 3600)
    assert params["endtime"] == str(NOW)

    assert (
        value(fams, "whm_email_messages", user="alpha", domain="alpha.example", result="sent") == 14
    )
    # two records for beta are summed into one series
    assert (
        value(fams, "whm_email_messages", user="beta", domain="beta.example", result="sent") == 1000
    )
    assert (
        value(fams, "whm_email_messages", user="beta", domain="beta.example", result="failed")
        == 450
    )
    assert value(fams, "whm_email_reached_max_per_hour", user="beta") == 1
    assert value(fams, "whm_email_reached_max_defer_fail", user="beta") == 1
    assert value(fams, "whm_email_reached_max_per_hour", user="alpha") == 0
    assert value(fams, "whm_email_window_seconds") == 3600
