"""Safe access to the logged-in LinkedIn profile.

Firefox assumes it owns its profile directory. Two Camoufox instances pointed
at the same one race on cookies.sqlite, and the loser's write wins — which is
how a live li_at kept disappearing while a sweep was running and an apply ran
alongside it. The session was not being revoked by LinkedIn at all; it was
being overwritten locally.

Two kinds of user, so two rules:

  writer  — the sweep and the manual login. They need their cookie refreshes
            to persist, so they use the real directory, one at a time, under
            an exclusive lock.
  reader  — apply-URL resolution and Easy Apply. They need to BE logged in but
            have nothing worth saving, so they get a throwaway copy. They can
            run while a sweep is in progress and cannot corrupt anything.
"""
import contextlib
import fcntl
import os
import shutil
import tempfile

from config import PROFILE_DIR

LOCK_PATH = os.path.join(os.path.dirname(PROFILE_DIR), ".li_profile.lock")
BACKUP_DIR = PROFILE_DIR + ".bak"


def _drop_stale_locks(path: str) -> None:
    for name in ("lock", ".parentlock"):
        with contextlib.suppress(OSError):
            os.remove(os.path.join(path, name))


def has_li_at(path: str = PROFILE_DIR) -> bool:
    import sqlite3

    src = os.path.join(path, "cookies.sqlite")
    if not os.path.exists(src):
        return False
    tmp = tempfile.mktemp()
    try:
        shutil.copy(src, tmp)
        for ext in ("-wal", "-shm"):
            if os.path.exists(src + ext):
                shutil.copy(src + ext, tmp + ext)
        return "li_at" in {
            r[0] for r in sqlite3.connect(tmp).execute(
                "select name from moz_cookies where host like '%linkedin%'")
        }
    except Exception:
        return False
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


def heal_from_backup() -> bool:
    """Put back a session that was clobbered locally.

    Only ever runs when the live profile has lost li_at and the backup still
    has it, which means the cookie was destroyed here rather than invalidated
    by LinkedIn. Loud on purpose: silently papering over this would hide a
    real bug.
    """
    if has_li_at(PROFILE_DIR):
        return False
    if not has_li_at(BACKUP_DIR):
        return False
    print("!! li_at missing from the live profile but present in the backup — "
          "restoring (something overwrote the cookie jar locally)")
    shutil.rmtree(PROFILE_DIR, ignore_errors=True)
    shutil.copytree(BACKUP_DIR, PROFILE_DIR)
    _drop_stale_locks(PROFILE_DIR)
    return True


def back_up() -> None:
    """Snapshot a known-good profile. Called after a clean writer session."""
    if not has_li_at(PROFILE_DIR):
        return
    tmp = BACKUP_DIR + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(PROFILE_DIR, tmp)
    shutil.rmtree(BACKUP_DIR, ignore_errors=True)
    os.rename(tmp, BACKUP_DIR)


@contextlib.contextmanager
def writer(timeout_note: str = "another LinkedIn browser is running"):
    """Exclusive use of the real profile. Refreshed cookies persist."""
    heal_from_backup()
    f = open(LOCK_PATH, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        raise RuntimeError(timeout_note)
    try:
        _drop_stale_locks(PROFILE_DIR)
        yield PROFILE_DIR
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()
        with contextlib.suppress(Exception):
            back_up()


@contextlib.contextmanager
def reader():
    """A disposable copy of the profile — authenticated, but safe to run
    beside a sweep because nothing it writes is kept."""
    heal_from_backup()
    tmp = tempfile.mkdtemp(prefix="li_lease_")
    dest = os.path.join(tmp, "profile")
    try:
        shutil.copytree(PROFILE_DIR, dest)
        _drop_stale_locks(dest)
        yield dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
