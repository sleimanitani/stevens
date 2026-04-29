"""Tests for stevens_security.keyring_passphrase.

Uses keyring's in-memory backend so the host's actual keychain isn't touched.
"""

from __future__ import annotations

import keyring
import pytest
from keyring.backend import KeyringBackend

from stevens_security import keyring_passphrase


class InMemoryBackend(KeyringBackend):
    """Trivial in-memory backend for tests."""

    priority = 100  # arbitrary; we install it explicitly

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._store.pop((service, username), None)


@pytest.fixture
def in_memory_keyring(monkeypatch):
    backend = InMemoryBackend()
    # Monkeypatch via set_keyring so both keyring.get_keyring() AND the
    # high-level keyring.get_password()/set_password()/delete_password()
    # use this backend.
    original = keyring.get_keyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(original)


def test_set_and_get_round_trip(in_memory_keyring) -> None:
    keyring_passphrase.set(b"hunter2")
    assert keyring_passphrase.get() == b"hunter2"


def test_get_returns_none_when_unset(in_memory_keyring) -> None:
    assert keyring_passphrase.get() is None


def test_clear_removes(in_memory_keyring) -> None:
    keyring_passphrase.set(b"x")
    keyring_passphrase.clear()
    assert keyring_passphrase.get() is None


def test_get_returns_none_with_no_backend(monkeypatch) -> None:
    """If the active backend is the FailKeyring, get() returns None — not raise."""
    from keyring.backends.fail import Keyring as FailKeyring

    monkeypatch.setattr(keyring, "get_keyring", lambda: FailKeyring())
    assert keyring_passphrase.get() is None


def test_set_with_no_backend_raises(monkeypatch) -> None:
    """set() should raise loudly — `passphrase remember` needs to fail clearly."""
    from keyring.backends.fail import Keyring as FailKeyring

    monkeypatch.setattr(keyring, "get_keyring", lambda: FailKeyring())
    with pytest.raises(keyring_passphrase.KeyringUnavailable):
        keyring_passphrase.set(b"x")


def test_clear_with_no_backend_is_silent(monkeypatch) -> None:
    """clear() should not raise when no backend — `forget` should be safe to run."""
    from keyring.backends.fail import Keyring as FailKeyring

    monkeypatch.setattr(keyring, "get_keyring", lambda: FailKeyring())
    keyring_passphrase.clear()  # no exception
