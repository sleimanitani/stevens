"""Tests for stevens_security.bootstrap.migrate — psql-free migration runner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stevens_security.bootstrap.migrate import (
    _resolve_migrations_dir,
    apply_migrations,
    main,
)


def test_resolve_migrations_dir_explicit_arg(tmp_path: Path):
    assert _resolve_migrations_dir(str(tmp_path)) == tmp_path.resolve()


def test_resolve_migrations_dir_env(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("STEVENS_MIGRATIONS_DIR", raising=False)
    monkeypatch.setenv("STEVENS_MIGRATIONS_DIR", str(tmp_path))
    assert _resolve_migrations_dir(None) == tmp_path.resolve()


def test_resolve_migrations_dir_default(monkeypatch):
    monkeypatch.delenv("STEVENS_MIGRATIONS_DIR", raising=False)
    out = _resolve_migrations_dir(None)
    assert out.name == "migrations"
    assert out.parent.name == "resources"


def test_apply_migrations_orders_and_calls_progress(tmp_path: Path):
    (tmp_path / "002_b.sql").write_text("SELECT 2;")
    (tmp_path / "001_a.sql").write_text("SELECT 1;")
    (tmp_path / "010_c.sql").write_text("SELECT 10;")

    seen: list[str] = []
    fake_conn = MagicMock()
    fake_ctx = MagicMock(__enter__=lambda s: fake_conn, __exit__=lambda *a: None)

    with patch("psycopg.connect", return_value=fake_ctx) as connect:
        n = apply_migrations("postgres:///x", tmp_path, on_progress=seen.append)

    assert n == 3
    assert seen == ["001_a.sql", "002_b.sql", "010_c.sql"]
    connect.assert_called_once_with("postgres:///x", autocommit=True)
    assert fake_conn.execute.call_count == 3


def test_main_no_dsn_returns_2(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    rc = main([])
    assert rc == 2
    err = capsys.readouterr().err
    assert "DATABASE_URL not set" in err


def test_main_missing_dir_returns_2(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgres:///x")
    rc = main([str(tmp_path / "absent")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "migrations directory not found" in err


def test_main_empty_dir_returns_0(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgres:///x")
    rc = main([str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no migrations to apply" in out


def test_main_applies_and_prints(monkeypatch, tmp_path: Path, capsys):
    (tmp_path / "001_a.sql").write_text("SELECT 1;")
    (tmp_path / "002_b.sql").write_text("SELECT 2;")
    monkeypatch.setenv("DATABASE_URL", "postgresql:///x")

    fake_conn = MagicMock()
    fake_ctx = MagicMock(__enter__=lambda s: fake_conn, __exit__=lambda *a: None)
    with patch("psycopg.connect", return_value=fake_ctx):
        rc = main([str(tmp_path)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "applying 2 migration(s) against postgresql://" in out
    assert "001_a.sql" in out
    assert "002_b.sql" in out
    assert "done." in out


@pytest.mark.skipif(
    "DATABASE_URL" not in __import__("os").environ,
    reason="integration test — requires real Postgres",
)
def test_apply_real_migrations_idempotent():
    """Applying the actual repo migrations twice in a row is a no-op the second time."""
    import os

    repo_mig = Path(__file__).resolve().parents[2] / "resources" / "migrations"
    n1 = apply_migrations(os.environ["DATABASE_URL"], repo_mig)
    n2 = apply_migrations(os.environ["DATABASE_URL"], repo_mig)
    assert n1 == n2 > 0
