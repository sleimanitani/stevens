"""Shared preflight predicates used by both ``demiurge bootstrap`` and
``demiurge doctor``.

v0.10 step 5. Pulled out of ``cli_bootstrap.py`` so the two consumers can
share the docker-group check (which has different policies — bootstrap
hard-fails, doctor warns) without duplicating the detection logic.

These functions are deliberately pure and side-effect-free: they read
``/etc/group`` / ``/etc/passwd`` via the standard library and return
booleans. No subprocesses, no I/O against Postgres. Cheap enough to call
on every ``stevens`` invocation if needed.
"""

from __future__ import annotations

import grp
import os
from typing import Optional


def in_docker_group(user: Optional[str] = None) -> bool:
    """``True`` if the running OS user is in the ``docker`` group.

    Checks supplementary group membership *and* the user's primary group,
    so a user whose primary group is somehow ``docker`` is also caught.

    STEVENS.md §2 Principle 14: docker-group membership is functionally
    passwordless root and is incompatible with running Stevens. Bootstrap
    treats this as a hard failure. Doctor reports it as a warning (the
    operator may have docker installed for unrelated reasons; we don't
    refuse to run, we just inform).

    Returns ``False`` if there is no ``docker`` group on this system, or
    if we can't determine the user.
    """
    me = user or os.environ.get("USER") or os.environ.get("LOGNAME")
    if not me:
        return False

    try:
        members = grp.getgrnam("docker").gr_mem
    except KeyError:
        return False
    if me in members:
        return True

    try:
        import pwd

        pw = pwd.getpwnam(me)
    except KeyError:
        return False
    try:
        primary = grp.getgrgid(pw.pw_gid).gr_name
    except KeyError:
        return False
    return primary == "docker"


def docker_group_removal_hint() -> str:
    """Return the exact one-liner for the operator to leave the docker group.

    The ``newgrp`` re-exec is what makes the change effective in the current
    shell without a logout — useful for scripted CI fixers and just plain
    nicer for humans than "log out and back in."
    """
    return "sudo gpasswd -d $USER docker && newgrp $(id -gn)"
