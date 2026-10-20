"""Machine id and code version, stamped on every DB row."""
from __future__ import annotations

import os
import socket
import subprocess

from rmc.config import REPO_ROOT


def machine_id() -> str:
    """Recorded on every row; RMC_MACHINE_ID overrides the hostname."""
    return os.environ.get("RMC_MACHINE_ID") or socket.gethostname()


def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def git_is_dirty() -> bool:
    """True if there are uncommitted changes to tracked or untracked (non-ignored) files,
    ignoring results/ (simulation output is not code)."""
    return bool(_git("status", "--porcelain", "--", ".", ":(exclude)results"))


def git_dirty_or_none() -> bool | None:
    """True/False for a git repository, None if this is not one (or git is missing)."""
    try:
        return git_is_dirty()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def code_version() -> str:
    """Current git commit, with '-dirty' appended if the tree has uncommitted changes.
    Returns 'unknown' if this is not a git repository."""
    try:
        commit = _git("rev-parse", "--short=12", "HEAD")
        return commit + ("-dirty" if git_is_dirty() else "")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
