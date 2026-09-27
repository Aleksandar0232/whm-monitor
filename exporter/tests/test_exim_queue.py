import os

import pytest
from conftest import value

from whm_exporter.collectors.exim_queue import EximQueueCollector, parse_header_file

NOW = 1790000000


def write_header(
    directory,
    msg_id,
    *,
    login="alpha 1001 1001",
    sender="<user@alpha.example>",
    received=NOW - 60,
    options=(),
):
    directory.mkdir(parents=True, exist_ok=True)
    lines = [f"{msg_id}-H", login, sender, f"{received} 0", "-local", *options]
    lines += ["XX", "1", "someone@example.net", "", "042P Received: from alpha by server", ""]
    path = directory / f"{msg_id}-H"
    path.write_text("\n".join(lines))
    (directory / f"{msg_id}-D").write_text(f"{msg_id}-D\nbody\n")
    return path


@pytest.fixture
def spool(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    return root


def collect(spool, **kwargs):
    return EximQueueCollector(str(spool), clock=lambda: NOW, **kwargs).collect()


def test_empty_queue(spool):
    fams = collect(spool)
    assert value(fams, "whm_exim_queue_messages") == 0
    assert value(fams, "whm_exim_queue_oldest_message_age_seconds") == 0


def test_counts_in_split_spool_subdirs(spool):
    write_header(spool, "1aAAAA-000001-AA", received=NOW - 7200)
    write_header(spool / "B", "1aBBBB-000002-BB", options=["-frozen 1789999000"])
    write_header(
        spool / "C",
        "1aCCCC-000003-CC",
        login="mailnull 47 12",
        sender="<>",
    )
    write_header(
        spool / "C",
        "1aCCCC-000004-CC",
        login="mailnull 47 12",
        options=["-auth_id info@Shop.Example", "-frozen 1789999999"],
    )

    fams = collect(spool)
    assert value(fams, "whm_exim_queue_messages") == 4
    assert value(fams, "whm_exim_queue_frozen_messages") == 2
    assert value(fams, "whm_exim_queue_bounce_messages") == 1
    assert value(fams, "whm_exim_queue_oldest_message_age_seconds") == 7200
    assert value(fams, "whm_exim_queue_messages_by_local_user", user="alpha") == 2
    assert value(fams, "whm_exim_queue_messages_by_local_user", user="mailnull") == 2
    assert value(fams, "whm_exim_queue_messages_by_auth_domain", domain="shop.example") == 1


def test_frozen_flag_after_acl_variable_value(tmp_path):
    # An ACL variable's value sits on its own line that does not start with "-";
    # the -frozen flag after it must still be found.
    path = tmp_path / "id-H"
    path.write_text(
        "id-H\nalpha 1 1\n<a@b.c>\n1790000000 0\n"
        "-aclm _spam 5\nhello\n-frozen 1790000100\n"
        "XX\n1\nx@y.z\n\n030  Subject: -frozen not a flag\n"
    )
    assert parse_header_file(str(path))["frozen"] is True


def test_header_lines_are_not_parsed_as_flags(tmp_path):
    path = tmp_path / "id-H"
    path.write_text(
        "id-H\nalpha 1 1\n<a@b.c>\n1790000000 0\n-local\n"
        "XX\n1\nx@y.z\n\n030  Subject: hi\n-frozen 1\n"
    )
    assert parse_header_file(str(path))["frozen"] is False


def test_top_n_limits_label_cardinality(spool):
    for i in range(5):
        write_header(spool, f"1aUSER{i}-00000{i}-AA", login=f"user{i} 100{i} 100{i}")
    write_header(spool, "1aUSER0-000009-AA", login="user0 1000 1000")
    fams = collect(spool, top_n=2)
    users = {s.labels["user"] for f in fams for s in f.samples if "user" in s.labels}
    assert len(users) == 2
    assert "user0" in users


def test_truncation(spool):
    for i in range(5):
        write_header(spool, f"1aMSG{i}-00000{i}-AA")
    fams = collect(spool, max_files=3)
    assert value(fams, "whm_exim_queue_messages") == 3
    assert value(fams, "whm_exim_queue_scan_truncated") == 1


def test_missing_spool_dir_fails(tmp_path):
    with pytest.raises(RuntimeError, match="not found"):
        EximQueueCollector(str(tmp_path / "nope")).collect()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_unreadable_spool_fails(spool):
    path = write_header(spool, "1aAAAA-000001-AA")
    path.chmod(0)
    with pytest.raises(PermissionError, match="EXIM_SPOOL_GID"):
        collect(spool)
