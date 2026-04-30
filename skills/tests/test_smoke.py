"""Smoke test — package imports and version is set."""

import skills


def test_version_is_set():
    assert skills.__version__ == "0.1.0"


def test_submodules_importable():
    import skills.registry
    import skills.retrieval
    import skills.playbooks.loader
    import skills.tools

    assert all(m is not None for m in (
        skills.registry, skills.retrieval, skills.playbooks.loader, skills.tools
    ))
