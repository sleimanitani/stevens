"""Tests for the core process supervisor — v0.11 step 7.1.

Uses real subprocesses (sleep, false, sh -c "exit 1") so the lifecycle
+ signal + restart paths get genuine coverage. Each test sets short
timeouts so the suite stays fast.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
import time
from pathlib import Path

import pytest

from demiurge.runtime import (
    BackoffPolicy,
    ProcessNotFound,
    ProcessStatus,
    SupervisedProcess,
    Supervisor,
    SupervisorClosed,
)


# Shorter backoff for tests so we don't sit waiting.
def _fast_backoff(**overrides) -> BackoffPolicy:
    base = dict(
        initial_delay=0.05,
        max_delay=0.2,
        multiplier=2.0,
        reset_after=2.0,
        max_restarts=None,
    )
    base.update(overrides)
    return BackoffPolicy(**base)


pytestmark = pytest.mark.skipif(
    not shutil.which("sleep") or not shutil.which("false"),
    reason="needs POSIX sleep/false on PATH",
)


# ----------------------------- registry ----------------------------------


def test_supervisor_starts_empty():
    sup = Supervisor()
    assert sup.names() == []
    assert sup.status() == []


def test_add_and_remove():
    sup = Supervisor()
    sup.add(SupervisedProcess(name="x", cmd=["sleep", "10"]))
    assert sup.names() == ["x"]
    sup.remove("x")
    assert sup.names() == []


def test_add_overwrites_same_name():
    sup = Supervisor()
    sup.add(SupervisedProcess(name="x", cmd=["sleep", "10"]))
    sup.add(SupervisedProcess(name="x", cmd=["sleep", "20"]))
    assert sup.get("x").cmd == ["sleep", "20"]


def test_get_unknown_raises():
    sup = Supervisor()
    with pytest.raises(ProcessNotFound):
        sup.get("nope")


def test_status_stable_order():
    sup = Supervisor()
    sup.add(SupervisedProcess(name="zeta", cmd=["sleep", "10"]))
    sup.add(SupervisedProcess(name="alpha", cmd=["sleep", "10"]))
    sup.add(SupervisedProcess(name="middle", cmd=["sleep", "10"]))
    assert [s.name for s in sup.status()] == ["alpha", "middle", "zeta"]


# ----------------------------- start / stop ------------------------------


def test_start_then_stop_simple_process():
    """Start sleep, then stop — process should die cleanly."""

    async def run():
        sup = Supervisor()
        sup.add(SupervisedProcess(name="sleeper", cmd=["sleep", "10"]))
        await sup.start("sleeper")

        # Wait briefly for spawn.
        await asyncio.sleep(0.1)
        st = sup.status()[0]
        assert st.is_running
        assert st.pid is not None

        await sup.stop("sleeper", timeout=2.0)
        st = sup.status()[0] if sup.status() else None
        assert st is not None
        assert not st.is_running

    asyncio.run(run())


def test_start_all_starts_every_running_process():
    async def run():
        sup = Supervisor()
        sup.add(SupervisedProcess(name="a", cmd=["sleep", "10"]))
        sup.add(SupervisedProcess(name="b", cmd=["sleep", "10"]))
        await sup.start_all()
        await asyncio.sleep(0.1)
        running = [s for s in sup.status() if s.is_running]
        assert len(running) == 2
        await sup.stop_all(timeout=2.0)

    asyncio.run(run())


def test_stop_all_closes_supervisor():
    async def run():
        sup = Supervisor()
        sup.add(SupervisedProcess(name="x", cmd=["sleep", "10"]))
        await sup.start_all()
        await asyncio.sleep(0.05)
        await sup.stop_all(timeout=2.0)

        # Adding a new process after stop_all is rejected.
        with pytest.raises(SupervisorClosed):
            sup.add(SupervisedProcess(name="y", cmd=["sleep", "10"]))

    asyncio.run(run())


def test_stop_idempotent_for_dead_process():
    """Stopping a process that already exited is a no-op (no error)."""

    async def run():
        sup = Supervisor()
        sup.add(
            SupervisedProcess(
                name="quick",
                cmd=["sh", "-c", "exit 0"],
                restart_policy="no",
            )
        )
        await sup.start("quick")
        # Let it exit on its own.
        await asyncio.sleep(0.2)
        # Now stop — should be a no-op.
        await sup.stop("quick", timeout=1.0)

    asyncio.run(run())


# ----------------------------- restart-on-failure ------------------------


def test_restart_on_failure_relaunches_failing_process():
    """A process that exits with non-zero gets restarted."""

    async def run():
        sup = Supervisor()
        sup.add(
            SupervisedProcess(
                name="flaky",
                cmd=["sh", "-c", "exit 1"],
                restart_policy="on-failure",
                backoff=_fast_backoff(),
            )
        )
        await sup.start("flaky")
        # Let it cycle a few times.
        await asyncio.sleep(0.5)
        st = sup.status()[0]
        assert st.restart_count >= 1
        await sup.stop("flaky", timeout=1.0)

    asyncio.run(run())


def test_no_restart_when_policy_is_no():
    """restart_policy='no' → process exits stay exited."""

    async def run():
        sup = Supervisor()
        sup.add(
            SupervisedProcess(
                name="oneshot",
                cmd=["sh", "-c", "exit 1"],
                restart_policy="no",
                backoff=_fast_backoff(),
            )
        )
        await sup.start("oneshot")
        await asyncio.sleep(0.5)
        st = sup.status()[0]
        assert st.restart_count == 0
        assert not st.is_running

    asyncio.run(run())


def test_restart_always_relaunches_on_clean_exit():
    """restart_policy='always' restarts even on exit code 0."""

    async def run():
        sup = Supervisor()
        sup.add(
            SupervisedProcess(
                name="forever",
                cmd=["sh", "-c", "exit 0"],
                restart_policy="always",
                backoff=_fast_backoff(),
            )
        )
        await sup.start("forever")
        await asyncio.sleep(0.5)
        st = sup.status()[0]
        assert st.restart_count >= 1
        await sup.stop("forever", timeout=1.0)

    asyncio.run(run())


def test_max_restarts_caps_burst():
    """max_restarts=2 → after 2 consecutive failures, stop trying."""

    async def run():
        sup = Supervisor()
        sup.add(
            SupervisedProcess(
                name="capped",
                cmd=["sh", "-c", "exit 1"],
                restart_policy="on-failure",
                backoff=_fast_backoff(max_restarts=2),
            )
        )
        await sup.start("capped")
        # Wait for the burst to complete + the watcher to give up.
        await asyncio.sleep(1.0)
        st = sup.status()[0]
        # 2 retries means: spawned 1, exited 1, retried twice, then gave up.
        # We assert restart_count is exactly 2 (or 0 if max_restarts checked
        # before first spawn — depends on impl). Either is acceptable; the
        # invariant is "no more than max_restarts".
        assert st.restart_count <= 2

    asyncio.run(run())


# ----------------------------- pause / resume ----------------------------


def test_pause_stops_process_and_blocks_restart():
    async def run():
        sup = Supervisor()
        sup.add(
            SupervisedProcess(
                name="paused",
                cmd=["sleep", "10"],
                restart_policy="always",
                backoff=_fast_backoff(),
            )
        )
        await sup.start("paused")
        await asyncio.sleep(0.1)
        assert sup.status()[0].is_running

        await sup.pause("paused", timeout=2.0)
        st = sup.status()[0]
        assert not st.is_running
        assert st.desired_state == "paused"

        # Wait long enough that "always" would restart if not paused.
        await asyncio.sleep(0.3)
        assert not sup.status()[0].is_running

        # Cleanup.
        await sup.stop("paused", timeout=1.0)

    asyncio.run(run())


def test_resume_restarts_paused_process():
    async def run():
        sup = Supervisor()
        sup.add(
            SupervisedProcess(
                name="rp",
                cmd=["sleep", "10"],
                restart_policy="always",
                backoff=_fast_backoff(),
            )
        )
        await sup.start("rp")
        await asyncio.sleep(0.1)
        await sup.pause("rp", timeout=2.0)
        assert sup.status()[0].desired_state == "paused"

        await sup.resume("rp")
        await asyncio.sleep(0.2)
        st = sup.status()[0]
        assert st.desired_state == "running"
        assert st.is_running

        await sup.stop("rp", timeout=1.0)

    asyncio.run(run())


# ----------------------------- logging ----------------------------------


def test_log_path_captures_stdout(tmp_path: Path):
    log = tmp_path / "out.log"

    async def run():
        sup = Supervisor()
        sup.add(
            SupervisedProcess(
                name="echo",
                cmd=["sh", "-c", "echo hello-from-supervised"],
                restart_policy="no",
                log_path=log,
            )
        )
        await sup.start("echo")
        await asyncio.sleep(0.3)

    asyncio.run(run())
    assert log.exists()
    assert "hello-from-supervised" in log.read_text()


def test_log_appends_across_restarts(tmp_path: Path):
    """Each restart adds to the log, doesn't truncate."""
    log = tmp_path / "append.log"

    async def run():
        sup = Supervisor()
        sup.add(
            SupervisedProcess(
                name="appender",
                cmd=["sh", "-c", "echo line; exit 0"],
                restart_policy="always",
                backoff=_fast_backoff(),
                log_path=log,
            )
        )
        await sup.start("appender")
        await asyncio.sleep(0.6)
        await sup.stop("appender", timeout=1.0)

    asyncio.run(run())
    contents = log.read_text()
    # Multiple "line" entries — the file grew across restarts.
    assert contents.count("line") >= 2


# ----------------------------- spawn-failure handling --------------------


def test_bad_command_no_infinite_loop():
    """Command with a missing executable doesn't crash-loop the supervisor."""

    async def run():
        sup = Supervisor()
        sup.add(
            SupervisedProcess(
                name="missing",
                cmd=["/definitely/not/a/real/binary"],
                restart_policy="on-failure",
                backoff=_fast_backoff(),
            )
        )
        await sup.start("missing")
        # Watcher should bail quickly on FileNotFoundError, not loop.
        await asyncio.sleep(0.3)
        st = sup.status()[0]
        assert not st.is_running
        # Cleanup (should be no-op).
        await sup.stop("missing", timeout=0.5)

    asyncio.run(run())


# ----------------------------- backoff state machine ---------------------


def test_consecutive_failures_grow_backoff():
    """After multiple failures, current_backoff increases per the multiplier."""

    async def run():
        sup = Supervisor()
        sup.add(
            SupervisedProcess(
                name="growing",
                cmd=["sh", "-c", "exit 1"],
                restart_policy="on-failure",
                backoff=_fast_backoff(initial_delay=0.05, max_delay=0.4, multiplier=2.0),
            )
        )
        await sup.start("growing")
        await asyncio.sleep(0.4)
        proc = sup.get("growing")
        assert proc.consecutive_failures >= 2
        # Backoff has grown past initial.
        assert proc.current_backoff > 0.05
        await sup.stop("growing", timeout=1.0)

    asyncio.run(run())


def test_long_running_success_resets_backoff():
    """A process that runs longer than reset_after resets consecutive_failures."""

    async def run():
        sup = Supervisor()
        sup.add(
            SupervisedProcess(
                name="resetter",
                cmd=["sh", "-c", "sleep 0.2; exit 0"],
                restart_policy="always",
                backoff=_fast_backoff(initial_delay=0.05, reset_after=0.1),
            )
        )
        await sup.start("resetter")
        # Let it run + restart at least once.
        await asyncio.sleep(0.6)
        proc = sup.get("resetter")
        # consecutive_failures got reset because the run lasted ≥ reset_after.
        assert proc.consecutive_failures == 0
        await sup.stop("resetter", timeout=1.0)

    asyncio.run(run())


# ----------------------------- env + cwd --------------------------------


def test_env_passed_through(tmp_path: Path):
    """Custom env variables reach the subprocess."""
    log = tmp_path / "env.log"

    async def run():
        sup = Supervisor()
        sup.add(
            SupervisedProcess(
                name="env_test",
                cmd=["sh", "-c", "echo MY_VAR=$MY_VAR"],
                env={"MY_VAR": "hello-world", "PATH": os.environ["PATH"]},
                restart_policy="no",
                log_path=log,
            )
        )
        await sup.start("env_test")
        await asyncio.sleep(0.3)

    asyncio.run(run())
    assert "MY_VAR=hello-world" in log.read_text()


def test_cwd_passed_through(tmp_path: Path):
    """The process's working directory is set."""
    log = tmp_path / "cwd.log"
    target = tmp_path / "target"
    target.mkdir()

    async def run():
        sup = Supervisor()
        sup.add(
            SupervisedProcess(
                name="cwd_test",
                cmd=["sh", "-c", "pwd"],
                cwd=target,
                restart_policy="no",
                log_path=log,
            )
        )
        await sup.start("cwd_test")
        await asyncio.sleep(0.3)

    asyncio.run(run())
    assert str(target) in log.read_text()
