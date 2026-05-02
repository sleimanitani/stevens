"""Tests for the stevens admin CLI."""

import base64
import io
import os

import nacl.signing
import pytest

from demiurge.cli import main as cli_main


PASSPHRASE = "correct horse battery staple"


@pytest.fixture
def passphrase_env(monkeypatch):
    monkeypatch.setenv("STEVENS_PASSPHRASE", PASSPHRASE)
    yield


def run(capsys, argv, *, stdin_bytes: bytes = b"") -> tuple[int, str, str]:
    # Drain anything captured before this invocation so the return values
    # reflect only this call's output.
    capsys.readouterr()
    if stdin_bytes:
        import sys

        sys.stdin = io.TextIOWrapper(io.BytesIO(stdin_bytes))
        sys.stdin.buffer = sys.stdin.buffer  # type: ignore[attr-defined]
    rc = cli_main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_secrets_init_and_list_empty(tmp_path, passphrase_env, capsys):
    root = tmp_path / "vault"
    rc, out, _ = run(capsys, ["secrets", "init", "--root", str(root)])
    assert rc == 0
    assert "initialized" in out
    rc, out, _ = run(capsys, ["secrets", "list", "--root", str(root)])
    assert rc == 0
    assert "(no secrets)" in out


def test_secrets_add_from_file_and_list(tmp_path, passphrase_env, capsys):
    root = tmp_path / "vault"
    cli_main(["secrets", "init", "--root", str(root)])
    payload = tmp_path / "api-key"
    payload.write_bytes(b"sk-abc123")
    rc, out, _ = run(
        capsys,
        [
            "secrets",
            "add",
            "anthropic.api_key",
            "--root",
            str(root),
            "--from-file",
            str(payload),
            "--metadata",
            "env=prod",
            "--rotate-by-days",
            "30",
        ],
    )
    assert rc == 0
    assert "added" in out
    rc, out, _ = run(capsys, ["secrets", "list", "--root", str(root)])
    assert rc == 0
    assert "anthropic.api_key" in out
    assert "live" in out


def test_secrets_add_then_rotate_then_revoke(tmp_path, passphrase_env, capsys):
    root = tmp_path / "vault"
    cli_main(["secrets", "init", "--root", str(root)])
    payload = tmp_path / "v1"
    payload.write_bytes(b"v1")
    cli_main(
        [
            "secrets", "add", "db.password",
            "--root", str(root),
            "--from-file", str(payload),
        ]
    )
    rc, out, _ = run(capsys, ["secrets", "list", "--root", str(root)])
    assert rc == 0
    secret_id = out.split()[0]

    new_payload = tmp_path / "v2"
    new_payload.write_bytes(b"v2")
    rc, out, _ = run(
        capsys,
        [
            "secrets", "rotate", secret_id,
            "--root", str(root),
            "--from-file", str(new_payload),
        ],
    )
    assert rc == 0
    assert "rotated_from=" in out

    # Default list hides tombstoned. --all shows both.
    rc, out, _ = run(capsys, ["secrets", "list", "--root", str(root)])
    assert rc == 0
    assert out.count("db.password") == 1
    rc, out, _ = run(capsys, ["secrets", "list", "--root", str(root), "--all"])
    assert out.count("db.password") == 2

    rc, out, _ = run(
        capsys, ["secrets", "revoke", secret_id, "--root", str(root)]
    )
    # Revoking an already-tombstoned secret is idempotent.
    assert rc == 0


def test_secrets_delete_hard(tmp_path, passphrase_env, capsys):
    root = tmp_path / "vault"
    cli_main(["secrets", "init", "--root", str(root)])
    (tmp_path / "x").write_bytes(b"x")
    cli_main(
        [
            "secrets", "add", "temp",
            "--root", str(root),
            "--from-file", str(tmp_path / "x"),
        ]
    )
    rc, out, _ = run(capsys, ["secrets", "list", "--root", str(root)])
    sid = out.split()[0]
    rc, out, _ = run(capsys, ["secrets", "delete", sid, "--root", str(root)])
    assert rc == 0
    rc, out, _ = run(
        capsys, ["secrets", "list", "--root", str(root), "--all"]
    )
    assert "(no secrets)" in out


def test_secrets_wrong_passphrase_exits_nonzero(tmp_path, monkeypatch, capsys):
    root = tmp_path / "vault"
    monkeypatch.setenv("STEVENS_PASSPHRASE", PASSPHRASE)
    cli_main(["secrets", "init", "--root", str(root)])

    monkeypatch.setenv("STEVENS_PASSPHRASE", "bad")
    rc, _, err = run(capsys, ["secrets", "list", "--root", str(root)])
    assert rc != 0
    assert "wrong passphrase" in err.lower() or "error" in err.lower()


def test_agent_register_writes_pubkey(tmp_path, capsys):
    sk = nacl.signing.SigningKey.generate()
    pubkey_b64 = base64.b64encode(bytes(sk.verify_key)).decode("ascii")
    agents_yaml = tmp_path / "agents.yaml"
    rc, out, _ = run(
        capsys,
        [
            "agent", "register", "email_pm",
            "--pubkey-b64", pubkey_b64,
            "--agents-yaml", str(agents_yaml),
        ],
    )
    assert rc == 0
    assert "registered" in out

    import yaml

    data = yaml.safe_load(agents_yaml.read_text())
    names = [e["name"] for e in data["agents"]]
    assert "email_pm" in names


def test_agent_register_duplicate_rejected(tmp_path, capsys):
    sk = nacl.signing.SigningKey.generate()
    pubkey_b64 = base64.b64encode(bytes(sk.verify_key)).decode("ascii")
    agents_yaml = tmp_path / "agents.yaml"
    cli_main(
        [
            "agent", "register", "email_pm",
            "--pubkey-b64", pubkey_b64,
            "--agents-yaml", str(agents_yaml),
        ]
    )
    with pytest.raises(SystemExit, match="already registered"):
        cli_main(
            [
                "agent", "register", "email_pm",
                "--pubkey-b64", pubkey_b64,
                "--agents-yaml", str(agents_yaml),
            ]
        )


def test_agent_register_bad_base64_rejected(tmp_path, capsys):
    agents_yaml = tmp_path / "agents.yaml"
    with pytest.raises(SystemExit, match="not valid base64"):
        cli_main(
            [
                "agent", "register", "x",
                "--pubkey-b64", "!!!not base64!!!",
                "--agents-yaml", str(agents_yaml),
            ]
        )


def test_agent_register_wrong_key_length_rejected(tmp_path, capsys):
    # 16-byte random pretending to be a pubkey.
    pubkey_b64 = base64.b64encode(os.urandom(16)).decode("ascii")
    agents_yaml = tmp_path / "agents.yaml"
    with pytest.raises(SystemExit, match="32-byte"):
        cli_main(
            [
                "agent", "register", "x",
                "--pubkey-b64", pubkey_b64,
                "--agents-yaml", str(agents_yaml),
            ]
        )


def test_agent_register_from_pubkey_file(tmp_path, capsys):
    sk = nacl.signing.SigningKey.generate()
    pubkey_b64 = base64.b64encode(bytes(sk.verify_key)).decode("ascii")
    f = tmp_path / "email_pm.pub"
    f.write_text(pubkey_b64 + "\n")
    agents_yaml = tmp_path / "agents.yaml"
    rc, out, _ = run(
        capsys,
        [
            "agent", "register", "email_pm",
            "--pubkey-file", str(f),
            "--agents-yaml", str(agents_yaml),
        ],
    )
    assert rc == 0
