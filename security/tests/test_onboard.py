"""Tests for stevens_security.onboard — channel onboarding helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stevens_security.onboard import (
    OAuthClient,
    OnboardError,
    add_account_argv,
    ingest_google_oauth_client,
    ingest_whatsapp_app_secret,
    parse_google_client_json,
    shred_file,
)
from stevens_security.sealed_store import SealedStore, initialize_store


# --- parse_google_client_json ---


def test_parse_google_client_json_installed_shape() -> None:
    payload = json.dumps(
        {
            "installed": {
                "client_id": "abc.apps.googleusercontent.com",
                "client_secret": "GOCSPX-xyz",
            }
        }
    ).encode()
    c = parse_google_client_json(payload)
    assert c.client_id == "abc.apps.googleusercontent.com"
    assert c.client_secret == "GOCSPX-xyz"


def test_parse_google_client_json_web_shape_also_works() -> None:
    payload = json.dumps(
        {"web": {"client_id": "id", "client_secret": "sec"}}
    ).encode()
    c = parse_google_client_json(payload)
    assert c.client_id == "id"


def test_parse_google_client_json_invalid_json() -> None:
    with pytest.raises(OnboardError, match="not valid JSON"):
        parse_google_client_json(b"not-json")


def test_parse_google_client_json_missing_keys() -> None:
    with pytest.raises(OnboardError, match="missing 'installed' or 'web'"):
        parse_google_client_json(b"{}")


def test_parse_google_client_json_missing_secret() -> None:
    payload = json.dumps({"installed": {"client_id": "id"}}).encode()
    with pytest.raises(OnboardError, match="missing client_secret"):
        parse_google_client_json(payload)


# --- shred_file ---


def test_shred_file_removes_file(tmp_path: Path) -> None:
    f = tmp_path / "x"
    f.write_bytes(b"sensitive payload")
    shred_file(f, passes=2)
    assert not f.exists()


def test_shred_file_idempotent_on_missing(tmp_path: Path) -> None:
    f = tmp_path / "missing"
    shred_file(f)  # must not raise
    assert not f.exists()


# --- ingest_google_oauth_client ---


@pytest.fixture
def store(tmp_path: Path) -> SealedStore:
    initialize_store(tmp_path / "vault", b"test-passphrase")
    return SealedStore.unlock(tmp_path / "vault", b"test-passphrase")


def test_ingest_google_first_time(store: SealedStore) -> None:
    client = OAuthClient(client_id="cid", client_secret="csec")
    outcome = ingest_google_oauth_client(
        store, namespace="gmail", client=client, rotate=False
    )
    assert outcome == "ingested"
    assert store.get_by_name("gmail.oauth_client.id") == b"cid"
    assert store.get_by_name("gmail.oauth_client.secret") == b"csec"


def test_ingest_google_idempotent_no_rotate(store: SealedStore) -> None:
    client = OAuthClient(client_id="cid", client_secret="csec")
    ingest_google_oauth_client(store, namespace="gmail", client=client, rotate=False)
    # Second call without rotate is a no-op.
    outcome = ingest_google_oauth_client(
        store,
        namespace="gmail",
        client=OAuthClient(client_id="cid2", client_secret="csec2"),
        rotate=False,
    )
    assert outcome == "already_present"
    assert store.get_by_name("gmail.oauth_client.id") == b"cid"  # unchanged


def test_ingest_google_rotate(store: SealedStore) -> None:
    ingest_google_oauth_client(
        store,
        namespace="gmail",
        client=OAuthClient("cid", "csec"),
        rotate=False,
    )
    outcome = ingest_google_oauth_client(
        store,
        namespace="gmail",
        client=OAuthClient("cid2", "csec2"),
        rotate=True,
    )
    assert outcome == "rotated"
    assert store.get_by_name("gmail.oauth_client.id") == b"cid2"


def test_ingest_google_namespaces_isolated(store: SealedStore) -> None:
    """Storing a gmail client must not affect calendar's namespace."""
    ingest_google_oauth_client(
        store,
        namespace="gmail",
        client=OAuthClient("gid", "gsec"),
        rotate=False,
    )
    outcome = ingest_google_oauth_client(
        store,
        namespace="calendar",
        client=OAuthClient("cid", "csec"),
        rotate=False,
    )
    assert outcome == "ingested"
    assert store.get_by_name("gmail.oauth_client.id") == b"gid"
    assert store.get_by_name("calendar.oauth_client.id") == b"cid"


# --- ingest_whatsapp_app_secret ---


def test_ingest_whatsapp_first_time(store: SealedStore) -> None:
    outcome = ingest_whatsapp_app_secret(store, app_secret=b"meta-secret", rotate=False)
    assert outcome == "ingested"
    assert store.get_by_name("whatsapp_cloud.app_secret") == b"meta-secret"


def test_ingest_whatsapp_idempotent(store: SealedStore) -> None:
    ingest_whatsapp_app_secret(store, app_secret=b"meta-secret", rotate=False)
    outcome = ingest_whatsapp_app_secret(store, app_secret=b"new-secret", rotate=False)
    assert outcome == "already_present"
    assert store.get_by_name("whatsapp_cloud.app_secret") == b"meta-secret"


def test_ingest_whatsapp_rotate(store: SealedStore) -> None:
    ingest_whatsapp_app_secret(store, app_secret=b"old", rotate=False)
    outcome = ingest_whatsapp_app_secret(store, app_secret=b"new", rotate=True)
    assert outcome == "rotated"
    assert store.get_by_name("whatsapp_cloud.app_secret") == b"new"


# --- add_account_argv ---


def test_add_account_argv_known_channel() -> None:
    argv = add_account_argv("gmail", ["--id", "gmail.personal"])
    assert argv[1:] == ["-m", "gmail_adapter.add_account", "--id", "gmail.personal"]


def test_add_account_argv_unknown_channel() -> None:
    with pytest.raises(OnboardError, match="unknown channel"):
        add_account_argv("matrix", [])  # not a registered channel yet
