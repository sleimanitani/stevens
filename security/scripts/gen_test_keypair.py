"""Dev-only helper: generate an Ed25519 keypair for a caller.

Writes the private key (base64) to ``<name>.key`` in the current directory
and appends the public key to ``security/policy/agents.yaml``. Meant only
for local testing — the production flow is ``demiurge agent register <name>``
(step 14), which goes through the sealed secret store.

Usage::

    uv run python security/scripts/gen_test_keypair.py <name> [agents_yaml_path]
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import nacl.signing
import yaml


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: gen_test_keypair.py <caller_name> [agents_yaml_path]",
            file=sys.stderr,
        )
        return 2

    name = argv[1]
    agents_path = Path(
        argv[2]
        if len(argv) > 2
        else Path(__file__).resolve().parents[1] / "policy" / "agents.yaml"
    )

    sk = nacl.signing.SigningKey.generate()
    sk_b64 = base64.b64encode(bytes(sk)).decode("ascii")
    pk_b64 = base64.b64encode(bytes(sk.verify_key)).decode("ascii")

    key_path = Path.cwd() / f"{name}.key"
    key_path.write_text(sk_b64 + "\n")
    key_path.chmod(0o600)

    data: dict = {}
    if agents_path.exists():
        loaded = yaml.safe_load(agents_path.read_text()) or {}
        if isinstance(loaded, dict):
            data = loaded
    agents = data.get("agents") or []
    if any(entry.get("name") == name for entry in agents):
        print(f"agent {name!r} already registered in {agents_path}", file=sys.stderr)
        return 1
    agents.append({"name": name, "pubkey_b64": pk_b64})
    data["agents"] = agents
    agents_path.write_text(yaml.safe_dump(data, sort_keys=False))

    print(f"generated keypair for {name}")
    print(f"  private key: {key_path}  (chmod 0600)")
    print(f"  public key appended to: {agents_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
