"""Tests for the channels-list registry."""

from __future__ import annotations

from demiurge.channels_list import all_channels, render


def test_all_channels_includes_shipped_set():
    names = {c.code_id for c in all_channels()}
    # shipped today
    assert {"gmail", "calendar", "whatsapp_cloud", "signal"}.issubset(names)


def test_render_groups_by_status():
    out = render()
    assert "## shipped" in out
    assert "## planned" in out
    # the four shipped channels appear in shipped section
    shipped_section = out.split("## planned")[0]
    assert "Gmail" in shipped_section
    assert "Calendar" in shipped_section
    assert "Signal" in shipped_section


def test_render_shows_runbook_paths():
    out = render()
    assert "docs/runbooks/gmail.md" in out
    assert "docs/runbooks/signal.md" in out


def test_render_points_to_master_flow():
    out = render()
    assert "docs/runbooks/README.md" in out


def test_planned_channels_have_no_runbook():
    for c in all_channels():
        if c.status == "planned":
            assert c.runbook == "(no runbook yet)"
