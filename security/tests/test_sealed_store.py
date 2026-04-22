"""Tests for the sealed secret store."""

import json
import os
from datetime import datetime, timezone

import pytest

from stevens_security.sealed_store import (
    AlreadyExistsError,
    NotFoundError,
    SealedStore,
    SealedStoreError,
    UnlockError,
    initialize_store,
)


PASSPHRASE = b"correct horse battery staple"
WRONG_PASSPHRASE = b"incorrect horse battery staple"


def _ok_store(tmp_path):
    return initialize_store(tmp_path / "vault", PASSPHRASE)


def test_initialize_creates_master_and_vault(tmp_path):
    store = _ok_store(tmp_path)
    root = tmp_path / "vault"
    assert (root / "master.info").exists()
    assert (root / "vault.sealed").exists()
    assert store.list() == []


def test_initialize_refuses_overwrite_without_force(tmp_path):
    initialize_store(tmp_path / "vault", PASSPHRASE)
    with pytest.raises(SealedStoreError, match="already exists"):
        initialize_store(tmp_path / "vault", PASSPHRASE)


def test_initialize_force_overwrites(tmp_path):
    initialize_store(tmp_path / "vault", PASSPHRASE)
    store2 = initialize_store(tmp_path / "vault", PASSPHRASE, force=True)
    assert store2.list() == []


def test_master_info_not_world_readable(tmp_path):
    initialize_store(tmp_path / "vault", PASSPHRASE)
    master_info = tmp_path / "vault" / "master.info"
    mode = master_info.stat().st_mode & 0o777
    assert mode == 0o600


def test_add_and_get_roundtrip(tmp_path):
    store = _ok_store(tmp_path)
    ref = store.add("gmail.personal.oauth", b"access-token-xyz")
    assert ref.name == "gmail.personal.oauth"
    assert store.get(ref.id) == b"access-token-xyz"
    assert store.get_by_name("gmail.personal.oauth") == b"access-token-xyz"


def test_add_refuses_duplicate_live_name(tmp_path):
    store = _ok_store(tmp_path)
    store.add("api.key", b"v1")
    with pytest.raises(AlreadyExistsError):
        store.add("api.key", b"v2")


def test_add_after_revoke_is_allowed(tmp_path):
    store = _ok_store(tmp_path)
    r1 = store.add("api.key", b"v1")
    store.revoke(r1.id)
    r2 = store.add("api.key", b"v2")
    assert store.get_by_name("api.key") == b"v2"
    # Revoked one still exists in the store's full list.
    all_refs = store.list(include_tombstoned=True)
    assert {r.id for r in all_refs} == {r1.id, r2.id}


def test_list_excludes_tombstoned_by_default(tmp_path):
    store = _ok_store(tmp_path)
    r1 = store.add("a", b"1")
    r2 = store.add("b", b"2")
    store.revoke(r1.id)
    live = [r.id for r in store.list()]
    assert live == [r2.id]


def test_rotate_creates_new_and_tombstones_old(tmp_path):
    store = _ok_store(tmp_path)
    old = store.add("db.password", b"old-pw")
    new = store.rotate(old.id, b"new-pw")
    assert new.id != old.id
    assert new.name == "db.password"
    assert new.rotated_from == old.id
    # get_by_name returns the live one.
    assert store.get_by_name("db.password") == b"new-pw"
    # Old id still resolves to old value (useful during transition windows).
    assert store.get(old.id) == b"old-pw"
    # Old ref is now tombstoned.
    assert store.ref(old.id).is_tombstoned


def test_rotate_carries_over_metadata_by_default(tmp_path):
    store = _ok_store(tmp_path)
    old = store.add("db.password", b"old", metadata={"env": "prod"})
    new = store.rotate(old.id, b"new")
    assert new.metadata == {"env": "prod"}


def test_rotate_replaces_metadata_when_provided(tmp_path):
    store = _ok_store(tmp_path)
    old = store.add("db.password", b"old", metadata={"env": "prod"})
    new = store.rotate(old.id, b"new", metadata={"env": "prod", "rotated": True})
    assert new.metadata == {"env": "prod", "rotated": True}


def test_rotate_of_tombstoned_refused(tmp_path):
    store = _ok_store(tmp_path)
    r = store.add("a", b"1")
    store.revoke(r.id)
    with pytest.raises(SealedStoreError, match="tombstoned"):
        store.rotate(r.id, b"2")


def test_revoke_is_idempotent(tmp_path):
    store = _ok_store(tmp_path)
    r = store.add("a", b"1")
    r1 = store.revoke(r.id)
    r2 = store.revoke(r.id)
    assert r1.tombstoned_at == r2.tombstoned_at
    assert r2.is_tombstoned


def test_delete_removes_completely(tmp_path):
    store = _ok_store(tmp_path)
    r = store.add("temp", b"x")
    store.delete(r.id)
    with pytest.raises(NotFoundError):
        store.get(r.id)


def test_persistence_across_lock_unlock(tmp_path):
    store = _ok_store(tmp_path)
    r = store.add("persist.me", b"value-1", metadata={"k": "v"})
    # Drop reference, re-unlock from disk.
    del store
    reopened = SealedStore.unlock(tmp_path / "vault", PASSPHRASE)
    assert reopened.get_by_name("persist.me") == b"value-1"
    refs = reopened.list()
    assert len(refs) == 1 and refs[0].id == r.id
    assert refs[0].metadata == {"k": "v"}


def test_unlock_wrong_passphrase(tmp_path):
    initialize_store(tmp_path / "vault", PASSPHRASE)
    with pytest.raises(UnlockError, match="wrong passphrase"):
        SealedStore.unlock(tmp_path / "vault", WRONG_PASSPHRASE)


def test_unlock_missing_store(tmp_path):
    with pytest.raises(UnlockError, match="no sealed store"):
        SealedStore.unlock(tmp_path / "nothing-here", PASSPHRASE)


def test_unlock_tampered_master_info(tmp_path):
    initialize_store(tmp_path / "vault", PASSPHRASE)
    master = tmp_path / "vault" / "master.info"
    data = json.loads(master.read_text())
    # Flip a bit in the verifier — unlock must detect it.
    v = bytearray(__import__("base64").b64decode(data["verifier_b64"]))
    v[0] ^= 0x01
    data["verifier_b64"] = __import__("base64").b64encode(bytes(v)).decode("ascii")
    master.write_text(json.dumps(data))
    with pytest.raises(UnlockError):
        SealedStore.unlock(tmp_path / "vault", PASSPHRASE)


def test_unlock_tampered_vault(tmp_path):
    initialize_store(tmp_path / "vault", PASSPHRASE)
    sealed = tmp_path / "vault" / "vault.sealed"
    data = bytearray(sealed.read_bytes())
    # Flip a byte inside the ciphertext (skip the 24-byte nonce).
    data[30] ^= 0x01
    sealed.write_bytes(bytes(data))
    with pytest.raises(UnlockError):
        SealedStore.unlock(tmp_path / "vault", PASSPHRASE)


def test_get_by_name_of_tombstoned_refused_by_default(tmp_path):
    store = _ok_store(tmp_path)
    r = store.add("a", b"1")
    store.revoke(r.id)
    with pytest.raises(NotFoundError):
        store.get_by_name("a")
    # include_tombstoned=True → it still resolves.
    assert store.get_by_name("a", include_tombstoned=True) == b"1"


def test_non_bytes_value_rejected(tmp_path):
    store = _ok_store(tmp_path)
    with pytest.raises(SealedStoreError, match="must be bytes"):
        store.add("a", "not-bytes")  # type: ignore[arg-type]


def test_empty_name_rejected(tmp_path):
    store = _ok_store(tmp_path)
    with pytest.raises(SealedStoreError, match="non-empty"):
        store.add("", b"v")


def test_rotate_by_days_sets_rotate_by(tmp_path):
    fixed = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
    store = initialize_store(tmp_path / "v", PASSPHRASE)
    # Swap in a fixed clock for deterministic rotate_by.
    store._clock = lambda: fixed  # type: ignore[attr-defined]
    ref = store.add("api.key", b"x", rotate_by_days=90)
    assert ref.rotate_by == "2026-07-21T12:00:00+00:00"


def test_instance_cannot_be_direct_constructed(tmp_path):
    with pytest.raises(TypeError):
        SealedStore()  # type: ignore[call-arg]


def test_vault_file_mode_is_0600(tmp_path):
    store = _ok_store(tmp_path)
    store.add("a", b"1")
    vault = tmp_path / "vault" / "vault.sealed"
    mode = vault.stat().st_mode & 0o777
    assert mode == 0o600
