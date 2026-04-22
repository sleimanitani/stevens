"""Tests for the canonical msgpack encoder (moved from stevens_security in step 8)."""

import pytest

from shared.canonical import CanonicalEncodingError, canonical_encode


def test_determinism_key_order_does_not_matter():
    a = {"a": 1, "b": 2, "c": 3}
    b = {"c": 3, "a": 1, "b": 2}
    assert canonical_encode(a) == canonical_encode(b)


def test_determinism_nested():
    a = {"outer": {"a": 1, "b": {"x": "y", "m": "n"}}}
    b = {"outer": {"b": {"m": "n", "x": "y"}, "a": 1}}
    assert canonical_encode(a) == canonical_encode(b)


def test_preserves_list_order():
    a = {"xs": [1, 2, 3]}
    b = {"xs": [3, 2, 1]}
    assert canonical_encode(a) != canonical_encode(b)


def test_allows_str_int_bool_none_bytes():
    obj = {
        "s": "hello",
        "n": 42,
        "flag": True,
        "neg": False,
        "nothing": None,
        "blob": b"\x00\x01\x02",
    }
    out = canonical_encode(obj)
    assert isinstance(out, bytes) and len(out) > 0


def test_rejects_float():
    with pytest.raises(CanonicalEncodingError):
        canonical_encode({"x": 1.5})


def test_rejects_non_string_dict_key():
    with pytest.raises(CanonicalEncodingError):
        canonical_encode({1: "one"})


def test_accepts_tuples_normalized_to_lists():
    a = {"xs": (1, 2, 3)}
    b = {"xs": [1, 2, 3]}
    assert canonical_encode(a) == canonical_encode(b)


def test_golden_fixture_small():
    obj = {"v": 1, "caller": "email_pm", "ts": 0}
    expected = b"\x83\xa6caller\xa8email_pm\xa2ts\x00\xa1v\x01"
    assert canonical_encode(obj) == expected


def test_reexport_from_stevens_security_is_the_same_function():
    from stevens_security.canonical import canonical_encode as server_canonical

    assert server_canonical is canonical_encode
