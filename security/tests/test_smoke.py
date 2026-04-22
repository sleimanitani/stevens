"""Smoke test — verify the stevens_security package scaffolding imports cleanly.

This is the acceptance test for plans/v0.1-sec.md step 1. Real behavior for
each submodule is tested in later steps.
"""

from stevens_security import __version__


def test_package_version_present():
    assert __version__
    assert __version__.startswith("0.1.0")


def test_all_submodules_importable():
    from stevens_security import audit, identity, policy, server  # noqa: F401
    from stevens_security.capabilities import registry  # noqa: F401
