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
import signal
import tempfile
import time

from config import PROFILE_DIR

LOCK_PATH = os.path.join(os.path.dirname(PROFILE_DIR), ".li_profile.lock")
BACKUP_DIR = PROFILE_DIR + ".bak"

# A top-level Camoufox looks like:
#   camoufox-bin -no-remote -wait-for-browser -foreground -profile <dir> ...
# Its renderers carry -contentproc and exit with the parent, so only the parent
# is ever matched or signalled.
BROWSER_BIN = "camoufox-bin"
READER_PREFIX = os.path.join(tempfile.gettempdir(), "li_lease_")

# An apply that has not finished in 30 minutes is wedged, not slow.
ORPHAN_AGE = 1800
# Backstop past systemd's own TimeoutStartSec on the sweep (3h). A lease held
# longer than this is never going to be released on its own.
MAX_LEASE_SECONDS = 4 * 3600

_HZ = os.sysconf("SC_CLK_TCK")


def _drop_stale_locks(path: str) -> None:
    for name in ("lock", ".parentlock"):
        with contextlib.suppress(OSError):
            os.remove(os.path.join(path, name))


# ---------------------------------------------------------------------------
# Janitor
#
# A browser that crashes without dying keeps its profile locked forever, and if
# it is sitting on the real profile it also keeps corrupting the cookie jar.
# Nothing frees it on its own, so every writer sweeps before it starts.
# ---------------------------------------------------------------------------
def _boot_time() -> float:
    with contextlib.suppress(OSError, ValueError):
        with open("/proc/stat") as fh:
            for line in fh:
                if line.startswith("btime "):
                    return float(line.split()[1])
    return 0.0


_BOOT = _boot_time()


def _age(pid: int) -> float:
    """Seconds since the process started, from /proc/<pid>/stat field 22.
    comm can contain spaces and parens, so it is read past the last ')'."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            data = fh.read()
        fields = data[data.rindex(")") + 2:].split()
        return max(0.0, time.time() - (_BOOT + float(fields[19]) / _HZ))
    except (OSError, ValueError, IndexError):
        return 0.0


def _cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""


def browsers() -> list[tuple[int, str]]:
    """(pid, profile_dir) for every top-level Camoufox on the box.

    /proc-based, so this is a no-op on macOS. The box is Linux; a dev run on a
    laptop just skips reaping rather than failing.
    """
    found = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return found
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == os.getpid():
            continue
        cl = _cmdline(pid)
        if BROWSER_BIN not in cl or "-contentproc" in cl or "-profile " not in cl:
            continue
        found.append((pid, cl.split("-profile ", 1)[1].split(" ", 1)[0]))
    return found


def _kill(pid: int, why: str) -> None:
    """SIGTERM first, SIGKILL only if it will not go. Firefox flushes its cookie
    jar on a clean exit and truncates it on a hard one, so leading with SIGKILL
    is itself a way to destroy a good session."""
    print(f"!! reaping pid {pid} ({why})")
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(100):  # up to 10s to shut down cleanly
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except OSError:
            return
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)


def reap(aggressive: bool = False) -> int:
    """Kill browsers nothing owns any more. Returns how many.

    aggressive: also kill any browser on the REAL profile regardless of age.
    Only correct once the flock is held, because that is what proves no
    legitimate writer is running.
    """
    killed = 0
    for pid, prof in browsers():
        real = os.path.realpath(prof) == os.path.realpath(PROFILE_DIR)
        age = _age(pid)
        if real and aggressive:
            _kill(pid, "left on the real profile while the lease was free")
        elif real and age > MAX_LEASE_SECONDS:
            _kill(pid, f"on the real profile for {age / 3600:.1f}h")
        elif prof.startswith(READER_PREFIX) and age > ORPHAN_AGE:
            _kill(pid, f"orphaned apply browser, {age / 60:.0f} min old")
        else:
            continue
        killed += 1
    return killed


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


def _try_lock(f) -> bool:
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _stamp(f) -> None:
    f.seek(0)
    f.truncate()
    f.write(f"{os.getpid()} {time.time():.0f}\n")
    f.flush()


def lease_holder() -> tuple[int, float] | None:
    """(pid, seconds held) of whoever stamped the lock, or None when it is free
    or was stamped by a process that has since gone."""
    try:
        with open(LOCK_PATH) as fh:
            pid_s, since_s = fh.read().split()[:2]
        pid, since = int(pid_s), float(since_s)
    except (OSError, ValueError, IndexError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid, time.time() - since


@contextlib.contextmanager
def writer(timeout_note: str = "another LinkedIn browser is running"):
    """Exclusive use of the real profile. Refreshed cookies persist."""
    reap()
    f = open(LOCK_PATH, "a+")
    if not _try_lock(f):
        held = lease_holder()
        if held and held[1] < MAX_LEASE_SECONDS:
            f.close()
            raise RuntimeError(
                f"{timeout_note} (pid {held[0]}, {held[1] / 60:.0f} min in)")
        # Nobody stamped it, or the holder is wedged far past any real pass. A
        # lease that will never be released blocks every future run, so break
        # it: killing the holder is what makes the kernel drop its flock.
        if held:
            print(f"!! breaking a stuck lease held by pid {held[0]} for "
                  f"{held[1] / 3600:.1f}h")
            _kill(held[0], "wedged lease holder")
        if not _try_lock(f):
            f.close()
            raise RuntimeError(timeout_note)
    try:
        _stamp(f)
        # The flock is ours, so any browser still on the real profile is an
        # orphan by definition — no live writer could be using it.
        reap(aggressive=True)
        _drop_stale_locks(PROFILE_DIR)
        heal_from_backup()
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
    reap()
    heal_from_backup()
    tmp = tempfile.mkdtemp(prefix="li_lease_")
    dest = os.path.join(tmp, "profile")
    try:
        shutil.copytree(PROFILE_DIR, dest)
        _drop_stale_locks(dest)
        yield dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import sys

    if "--reap" in sys.argv:
        print(f"reaped {reap(aggressive='--force' in sys.argv)} browser(s)")
    else:
        print("live li_at  :", has_li_at(PROFILE_DIR))
        print("backup li_at:", has_li_at(BACKUP_DIR))
        h = lease_holder()
        print("lease       :",
              f"held by pid {h[0]} for {h[1] / 60:.0f} min" if h else "free")
        for pid, prof in browsers():
            print(f"  browser pid {pid}  age {_age(pid) / 60:.0f}m  {prof}")
