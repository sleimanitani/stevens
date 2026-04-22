"""Tests for shared.redactor."""

from shared.redactor import REDACTED, redact, redact_headers


def test_sensitive_key_redacted():
    out = redact({"password": "hunter2", "username": "sol"})
    assert out["username"] == "sol"
    assert out["password"].startswith("<REDACTED")


def test_api_key_variants():
    for k in ("api_key", "apikey", "X-API-Key", "ANTHROPIC_API_KEY"):
        out = redact({k: "sk-abc"})
        assert out[k].startswith("<REDACTED")


def test_nested_dict_redaction():
    out = redact({"outer": {"secret": "x", "safe": "y"}})
    assert out["outer"]["safe"] == "y"
    assert out["outer"]["secret"].startswith("<REDACTED")


def test_list_preserved():
    out = redact([{"token": "abc"}, {"value": "ok"}])
    assert out[0]["token"].startswith("<REDACTED")
    assert out[1]["value"] == "ok"


def test_bearer_token_in_string_redacted():
    s = "Authorization: Bearer ya29.abcdefghijklmnop-_1234567890"
    out = redact(s)
    assert "ya29" not in out


def test_jwt_redacted():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.signature_here_xyz"
    out = redact(f"before {jwt} after")
    assert jwt not in out
    assert REDACTED in out


def test_google_access_token_redacted():
    token = "ya29.a0AfH6SMBqW_long_token_value_here_1234567890"
    out = redact(token)
    assert "ya29" not in out


def test_high_entropy_string_redacted():
    long_b64 = "ABCDefghIJKL123456mnopQRStuvWXYZabc0def12ghi345"  # 47 chars, no spaces
    out = redact(long_b64)
    assert out == REDACTED


def test_normal_strings_preserved():
    assert redact("Hello, world") == "Hello, world"
    assert redact("sol@y76.io") == "sol@y76.io"
    assert redact("the quick brown fox jumps over the lazy dog") == (
        "the quick brown fox jumps over the lazy dog"
    )


def test_email_addresses_preserved():
    out = redact({"from": "sol@y76.io", "to": ["atheer@example.com"]})
    assert out["from"] == "sol@y76.io"
    assert out["to"] == ["atheer@example.com"]


def test_integers_and_none_passthrough():
    assert redact({"n": 42, "x": None, "flag": True}) == {"n": 42, "x": None, "flag": True}


def test_redact_headers_authorization():
    out = redact_headers([("Content-Type", "application/json"), ("Authorization", "Bearer abc123")])
    assert out[0] == ("Content-Type", "application/json")
    assert "REDACTED" in out[1][1]


def test_redact_headers_api_key():
    out = redact_headers([("X-Api-Key", "sk-abcdef")])
    assert out[0][1].startswith("<REDACTED")


def test_nested_sensitive_in_list_of_dicts():
    out = redact(
        [
            {"name": "public", "value": "visible"},
            {"name": "private", "api_key": "should-disappear"},
        ]
    )
    assert out[0]["value"] == "visible"
    assert "should-disappear" not in str(out[1])


def test_sig_field_redacted_by_name():
    out = redact({"sig": "z9fakeSigButLookLikeASig_really_opaque"})
    assert out["sig"].startswith("<REDACTED")


def test_signature_field_redacted_by_name():
    out = redact({"signature": "any value"})
    assert out["signature"].startswith("<REDACTED")


def test_client_secret_redacted():
    out = redact({"client_secret": "GOCSPX-abc123"})
    assert out["client_secret"].startswith("<REDACTED")


def test_tuple_preserves_type_and_redacts():
    out = redact(("normal", "ya29.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"))
    assert isinstance(out, tuple)
    assert out[0] == "normal"
    assert "ya29" not in out[1]


def test_replace_only_redacts_matching_spans_within_string():
    # A mixed string: clearly-sensitive sub-span should be redacted but the
    # rest of the message preserved (useful for trace context).
    s = "Call failed with token ya29.ABCDEFGHIJKLMNOPQRSTUVWXYZ — please retry"
    out = redact(s)
    assert "Call failed with token" in out
    assert "ya29" not in out
    assert "please retry" in out
