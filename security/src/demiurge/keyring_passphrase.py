"""OS keyring integration for the sealed-store passphrase.

Opt-in. The operator runs::

    demiurge passphrase remember

…to stash the passphrase in the OS keyring (libsecret on Linux, Keychain
on macOS, Credential Manager on Windows). On every subsequent operation
that needs the passphrase, the CLI and Enkidu's ``__main__`` consult the
keyring **before** falling back to ``$DEMIURGE_PASSPHRASE`` env or an
interactive prompt.

Trade-off: with keyring enabled, anyone with your unlocked desktop session
can also unlock the vault. Acceptable for a single-user laptop where the
threat model is "stop a compromised agent process from leaking tokens,"
not "stop someone with desktop access." On a shared host or server,
don't use this — leave it off, set ``DEMIURGE_PASSPHRASE`` from a deploy
secret, or just type at the prompt.
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

SERVICE = "demiurge-security"
ACCOUNT = "vault"


class KeyringUnavailable(Exception):
    """Raised when no usable keyring backend is configured on this host."""


def _get_keyring():
    """Return the active keyring module or raise KeyringUnavailable."""
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
    except ImportError as e:  # pragma: no cover — keyring is a hard dep
        raise KeyringUnavailable(f"keyring lib not installed: {e}") from e

    backend = keyring.get_keyring()
    # Walk through ChainerBackend → its first member, in case the active
    # backend is a chainer wrapping a Fail.
    chained = getattr(backend, "backends", None)
    if chained:
        backend = chained[0]
    if isinstance(backend, FailKeyring):
        raise KeyringUnavailable(
            "no keyring backend available — install gnome-keyring / KWallet "
            "/ macOS Keychain, or run with $DEMIURGE_PASSPHRASE env"
        )
    return keyring


def get() -> Optional[bytes]:
    """Return the stored passphrase as bytes, or None if not set / no backend.

    Never raises on a missing backend — that's a soft failure (caller falls
    back to env / prompt). It only raises if the backend is present but
    misbehaves.
    """
    try:
        kr = _get_keyring()
    except KeyringUnavailable:
        return None
    value = kr.get_password(SERVICE, ACCOUNT)
    if value is None:
        return None
    return value.encode("utf-8")


def set(passphrase: bytes) -> None:
    """Store ``passphrase`` in the keyring under (service, account)."""
    kr = _get_keyring()
    kr.set_password(SERVICE, ACCOUNT, passphrase.decode("utf-8"))


def clear() -> None:
    """Remove the stored passphrase. No-op if not present or no backend."""
    try:
        kr = _get_keyring()
    except KeyringUnavailable:
        return
    try:
        kr.delete_password(SERVICE, ACCOUNT)
    except Exception:  # noqa: BLE001 — different backends raise different things on missing entries
        pass
