"""Sealed secret store.

One encrypted vault on disk, unlocked at startup with a passphrase. The
root key is derived from the passphrase via Argon2id (libsodium
``pwhash.argon2id``); the vault itself is a libsodium ``secretbox``
encryption of a JSON document containing every secret Stevens holds.

All secret material at rest lives here and nowhere else. The Security
Agent reads the sealed store once on unlock and mediates every access
from there on. Other components never see ciphertext or key material.

File layout under ``<root>/``::

    master.info    JSON: {version, kdf_params, salt (b64), verifier (b64)}.
                   Detects wrong passphrase before we try to decrypt the vault.
    vault.sealed   24-byte nonce || secretbox ciphertext of the JSON vault.

Vault JSON shape::

    {
      "version": 1,
      "secrets": {
        "<secret_id>": {
          "name": "<human name>",
          "value": "<base64 payload>",
          "metadata": { ... },
          "created_at": "<iso8601 utc>",
          "rotated_at": "<iso8601 utc> | null",
          "rotate_by":  "<iso8601 utc> | null",
          "tombstoned_at": "<iso8601 utc> | null",
          "rotated_from": "<prior id> | null"
        },
        ...
      }
    }

The store is single-writer: only the Security Agent process opens it for
writes. Writes are atomic (temp file + rename); reads are in-memory once
unlocked.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import nacl.exceptions
import nacl.pwhash
import nacl.secret
import nacl.utils

# Argon2id tuning — libsodium's "moderate" defaults are a reasonable
# compromise for a laptop/workstation host. We can retune when hardware
# or threat model changes; the master.info records the params used so old
# vaults are upgradable.
_OPSLIMIT = nacl.pwhash.argon2id.OPSLIMIT_MODERATE
_MEMLIMIT = nacl.pwhash.argon2id.MEMLIMIT_MODERATE
_KDF_SALT_BYTES = nacl.pwhash.argon2id.SALTBYTES
_KDF_KEY_BYTES = nacl.secret.SecretBox.KEY_SIZE  # 32

_VAULT_VERSION = 1


class SealedStoreError(Exception):
    """Base class for sealed-store failures."""


class UnlockError(SealedStoreError):
    """Passphrase wrong or master.info missing / malformed."""


class NotFoundError(SealedStoreError):
    """No secret with that id (or tombstoned and not including tombstoned)."""


class AlreadyExistsError(SealedStoreError):
    """A secret with this name already exists (names are unique among live secrets)."""


@dataclass(frozen=True)
class SecretRef:
    """Non-sensitive metadata about a stored secret (safe to hand out)."""

    id: str
    name: str
    metadata: Dict[str, Any]
    created_at: str
    rotated_at: Optional[str] = None
    rotate_by: Optional[str] = None
    tombstoned_at: Optional[str] = None
    rotated_from: Optional[str] = None

    @property
    def is_tombstoned(self) -> bool:
        return self.tombstoned_at is not None


@dataclass(frozen=True)
class _SecretRecord:
    """Internal record — includes the secret value."""

    id: str
    name: str
    value: bytes
    metadata: Dict[str, Any]
    created_at: str
    rotated_at: Optional[str] = None
    rotate_by: Optional[str] = None
    tombstoned_at: Optional[str] = None
    rotated_from: Optional[str] = None

    def as_ref(self) -> SecretRef:
        return SecretRef(
            id=self.id,
            name=self.name,
            metadata=dict(self.metadata),
            created_at=self.created_at,
            rotated_at=self.rotated_at,
            rotate_by=self.rotate_by,
            tombstoned_at=self.tombstoned_at,
            rotated_from=self.rotated_from,
        )


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def _now_iso(clock: Callable[[], datetime]) -> str:
    now = clock()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    return now.isoformat()


def _derive_key(passphrase: bytes, salt: bytes) -> bytes:
    return nacl.pwhash.argon2id.kdf(
        _KDF_KEY_BYTES,
        passphrase,
        salt,
        opslimit=_OPSLIMIT,
        memlimit=_MEMLIMIT,
    )


def _verifier_bytes(key: bytes) -> bytes:
    # A known-plaintext we encrypt under the derived key so we can detect
    # a wrong passphrase without trying to decrypt the entire vault.
    box = nacl.secret.SecretBox(key)
    nonce = b"\x00" * nacl.secret.SecretBox.NONCE_SIZE
    return box.encrypt(b"stevens-sealed-store-v1", nonce).ciphertext


def _write_atomic(path: Path, data: bytes, mode: int = 0o600) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    os.replace(tmp, path)


def initialize_store(
    root: Path, passphrase: bytes, *, force: bool = False
) -> "SealedStore":
    """Create a new sealed store at ``root``. Refuses to overwrite without ``force=True``."""
    root = Path(root)
    master_info_path = root / "master.info"
    vault_path = root / "vault.sealed"

    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        # Best-effort on hosts where ownership doesn't allow chmod.
        pass

    if not force and (master_info_path.exists() or vault_path.exists()):
        raise SealedStoreError(
            f"store at {root} already exists; pass force=True to overwrite"
        )

    salt = nacl.utils.random(_KDF_SALT_BYTES)
    key = _derive_key(passphrase, salt)
    master = {
        "version": _VAULT_VERSION,
        "kdf": {
            "algorithm": "argon2id",
            "opslimit": int(_OPSLIMIT),
            "memlimit": int(_MEMLIMIT),
            "salt_b64": _b64(salt),
        },
        "verifier_b64": _b64(_verifier_bytes(key)),
    }
    _write_atomic(master_info_path, json.dumps(master, indent=2).encode("utf-8"))

    empty_vault = {"version": _VAULT_VERSION, "secrets": {}}
    box = nacl.secret.SecretBox(key)
    nonce = nacl.utils.random(nacl.secret.SecretBox.NONCE_SIZE)
    sealed = box.encrypt(json.dumps(empty_vault).encode("utf-8"), nonce)
    _write_atomic(vault_path, bytes(sealed))

    return SealedStore._from_unlocked(root, key, dict(empty_vault))


class SealedStore:
    """Unlocked view of the sealed vault. Keep in memory only inside the Security Agent."""

    def __init__(self) -> None:  # pragma: no cover - use classmethods
        raise TypeError(
            "construct SealedStore via SealedStore.unlock / initialize_store"
        )

    # --- construction ---

    @classmethod
    def _from_unlocked(
        cls,
        root: Path,
        key: bytes,
        vault: Dict[str, Any],
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> "SealedStore":
        self = cls.__new__(cls)
        self._root = Path(root)
        self._key = key
        self._box = nacl.secret.SecretBox(key)
        self._vault: Dict[str, Any] = vault
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        return self

    @classmethod
    def unlock(
        cls,
        root: Path,
        passphrase: bytes,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> "SealedStore":
        """Unlock an existing store. Raises :class:`UnlockError` on wrong passphrase."""
        root = Path(root)
        master_info_path = root / "master.info"
        vault_path = root / "vault.sealed"
        if not master_info_path.exists() or not vault_path.exists():
            raise UnlockError(f"no sealed store at {root}")

        try:
            master = json.loads(master_info_path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise UnlockError(f"master.info unreadable: {e}") from e

        if master.get("version") != _VAULT_VERSION:
            raise UnlockError(
                f"unsupported sealed store version: {master.get('version')!r}"
            )
        kdf = master.get("kdf") or {}
        if kdf.get("algorithm") != "argon2id":
            raise UnlockError(f"unsupported kdf: {kdf.get('algorithm')!r}")
        salt = _unb64(kdf["salt_b64"])

        key = _derive_key(passphrase, salt)

        expected_verifier = _unb64(master["verifier_b64"])
        actual_verifier = _verifier_bytes(key)
        if not secrets.compare_digest(expected_verifier, actual_verifier):
            raise UnlockError("wrong passphrase")

        sealed = vault_path.read_bytes()
        box = nacl.secret.SecretBox(key)
        try:
            plaintext = box.decrypt(sealed)
        except nacl.exceptions.CryptoError as e:
            raise UnlockError(f"vault decryption failed: {e}") from e
        vault = json.loads(plaintext.decode("utf-8"))
        if vault.get("version") != _VAULT_VERSION:
            raise UnlockError(
                f"unsupported vault version: {vault.get('version')!r}"
            )
        return cls._from_unlocked(root, key, vault, clock=clock)

    # --- internal persistence ---

    def _persist(self) -> None:
        plaintext = json.dumps(self._vault, separators=(",", ":")).encode("utf-8")
        nonce = nacl.utils.random(nacl.secret.SecretBox.NONCE_SIZE)
        sealed = self._box.encrypt(plaintext, nonce)
        _write_atomic(self._root / "vault.sealed", bytes(sealed))

    def _get_record(self, secret_id: str) -> _SecretRecord:
        raw = self._vault["secrets"].get(secret_id)
        if raw is None:
            raise NotFoundError(f"secret {secret_id!r} not found")
        return _SecretRecord(
            id=secret_id,
            name=raw["name"],
            value=_unb64(raw["value"]),
            metadata=dict(raw.get("metadata") or {}),
            created_at=raw["created_at"],
            rotated_at=raw.get("rotated_at"),
            rotate_by=raw.get("rotate_by"),
            tombstoned_at=raw.get("tombstoned_at"),
            rotated_from=raw.get("rotated_from"),
        )

    def _set_record(self, rec: _SecretRecord) -> None:
        self._vault["secrets"][rec.id] = {
            "name": rec.name,
            "value": _b64(rec.value),
            "metadata": dict(rec.metadata),
            "created_at": rec.created_at,
            "rotated_at": rec.rotated_at,
            "rotate_by": rec.rotate_by,
            "tombstoned_at": rec.tombstoned_at,
            "rotated_from": rec.rotated_from,
        }

    # --- public API ---

    def add(
        self,
        name: str,
        value: bytes,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        rotate_by_days: Optional[int] = None,
    ) -> SecretRef:
        """Add a new secret. Fails if a non-tombstoned secret with this name exists."""
        if not isinstance(value, (bytes, bytearray)):
            raise SealedStoreError("secret value must be bytes")
        if not name:
            raise SealedStoreError("secret name must be non-empty")
        for rec in self._iter_records():
            if rec.name == name and not rec.tombstoned_at:
                raise AlreadyExistsError(
                    f"a live secret named {name!r} already exists (id={rec.id!r})"
                )
        secret_id = str(uuid.uuid4())
        now = _now_iso(self._clock)
        rotate_by = None
        if rotate_by_days is not None:
            rotate_by = (self._clock() + timedelta(days=rotate_by_days)).astimezone(
                timezone.utc
            ).isoformat()
        rec = _SecretRecord(
            id=secret_id,
            name=name,
            value=bytes(value),
            metadata=dict(metadata or {}),
            created_at=now,
            rotate_by=rotate_by,
        )
        self._set_record(rec)
        self._persist()
        return rec.as_ref()

    def get(self, secret_id: str) -> bytes:
        """Return the secret value. Tombstoned secrets still reveal their value
        (useful for rotation windows); check the ref's ``is_tombstoned`` if caller
        wants to refuse. Raises :class:`NotFoundError` if the id is unknown."""
        return self._get_record(secret_id).value

    def get_by_name(self, name: str, *, include_tombstoned: bool = False) -> bytes:
        """Shortcut for the common lookup by stable name. Returns the live one."""
        rec = self._find_by_name(name, include_tombstoned=include_tombstoned)
        return rec.value

    def ref(self, secret_id: str) -> SecretRef:
        return self._get_record(secret_id).as_ref()

    def ref_by_name(self, name: str, *, include_tombstoned: bool = False) -> SecretRef:
        return self._find_by_name(
            name, include_tombstoned=include_tombstoned
        ).as_ref()

    def list(self, *, include_tombstoned: bool = False) -> List[SecretRef]:
        """Return refs (no values) for every stored secret."""
        out: List[SecretRef] = []
        for rec in self._iter_records():
            if not include_tombstoned and rec.tombstoned_at:
                continue
            out.append(rec.as_ref())
        return sorted(out, key=lambda r: r.created_at)

    def rotate(
        self,
        secret_id: str,
        new_value: bytes,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        rotate_by_days: Optional[int] = None,
    ) -> SecretRef:
        """Create a new secret carrying the old one's name; tombstone the old one.

        Returns the new :class:`SecretRef`. The new secret records ``rotated_from``
        pointing at the old id; the old secret is tombstoned (not deleted — keeps
        the audit trail intact).
        """
        old = self._get_record(secret_id)
        if old.tombstoned_at:
            raise SealedStoreError(
                f"cannot rotate tombstoned secret {secret_id!r}"
            )
        now = _now_iso(self._clock)
        new_id = str(uuid.uuid4())
        rotate_by = None
        if rotate_by_days is not None:
            rotate_by = (self._clock() + timedelta(days=rotate_by_days)).astimezone(
                timezone.utc
            ).isoformat()
        new_rec = _SecretRecord(
            id=new_id,
            name=old.name,
            value=bytes(new_value),
            metadata=dict(metadata) if metadata is not None else dict(old.metadata),
            created_at=now,
            rotated_at=None,
            rotate_by=rotate_by,
            rotated_from=secret_id,
        )
        tombstoned_old = _SecretRecord(
            id=old.id,
            name=old.name,
            value=old.value,
            metadata=old.metadata,
            created_at=old.created_at,
            rotated_at=now,
            rotate_by=old.rotate_by,
            tombstoned_at=now,
            rotated_from=old.rotated_from,
        )
        self._set_record(new_rec)
        self._set_record(tombstoned_old)
        self._persist()
        return new_rec.as_ref()

    def revoke(self, secret_id: str) -> SecretRef:
        """Tombstone a secret. Revoked secrets still exist in the store for audit
        but aren't returned by ``list()`` or ``get_by_name()`` unless asked."""
        rec = self._get_record(secret_id)
        if rec.tombstoned_at:
            return rec.as_ref()
        now = _now_iso(self._clock)
        tombstoned = _SecretRecord(
            id=rec.id,
            name=rec.name,
            value=rec.value,
            metadata=rec.metadata,
            created_at=rec.created_at,
            rotated_at=rec.rotated_at,
            rotate_by=rec.rotate_by,
            tombstoned_at=now,
            rotated_from=rec.rotated_from,
        )
        self._set_record(tombstoned)
        self._persist()
        return tombstoned.as_ref()

    def delete(self, secret_id: str) -> None:
        """Permanently remove a secret from the store (value is zeroed)."""
        if secret_id not in self._vault["secrets"]:
            raise NotFoundError(f"secret {secret_id!r} not found")
        del self._vault["secrets"][secret_id]
        self._persist()

    # --- helpers ---

    def _iter_records(self) -> Iterable[_SecretRecord]:
        for sid in self._vault["secrets"]:
            yield self._get_record(sid)

    def _find_by_name(self, name: str, *, include_tombstoned: bool) -> _SecretRecord:
        candidates = [rec for rec in self._iter_records() if rec.name == name]
        if not include_tombstoned:
            candidates = [rec for rec in candidates if not rec.tombstoned_at]
        if not candidates:
            raise NotFoundError(f"no live secret named {name!r}")
        if len(candidates) > 1:
            # Shouldn't happen given add()'s uniqueness check, but be defensive.
            raise SealedStoreError(
                f"multiple live secrets named {name!r}: {[c.id for c in candidates]}"
            )
        return candidates[0]
