"""Smoke test — verify the demiurge package scaffolding imports cleanly.

This is the acceptance test for plans/v0.1-sec.md step 1. Real behavior for
each submodule is tested in later steps.
"""

from demiurge import __version__


def test_package_version_present():
    assert __version__
    assert __version__.startswith("0.1.0")


def test_all_submodules_importable():
    from demiurge import audit, identity, policy, server  # noqa: F401
    from demiurge.capabilities import registry  # noqa: F401
