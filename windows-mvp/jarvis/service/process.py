"""Child processes that can be killed together with everything they started.

A hard stop must leave nothing running: not a PowerShell command, not a half-finished drag.
On Windows the child goes into a Job Object (KILL_ON_JOB_CLOSE, so it also dies with the app);
elsewhere it leads its own process group.
"""

import contextlib
import os
import signal
import subprocess
import sys
import threading

CREATE_NEW_PROCESS_GROUP = 0x00000200  # Ctrl+C in the app's console doesn't reach the child
CREATE_NO_WINDOW = 0x08000000


class Child:
    def __init__(self, cmd: list[str], *, cwd=None, env=None):
        kwargs = {}
        if sys.platform == "win32":
            info = subprocess.STARTUPINFO()
            info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            info.wShowWindow = 0  # SW_HIDE: a console the child (and its children) share, never shown
            kwargs = {"startupinfo": info, "creationflags": CREATE_NEW_PROCESS_GROUP}
        else:
            kwargs = {"start_new_session": True}
        self.popen = subprocess.Popen(
            cmd, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1, **kwargs,
        )
        self.pid = self.popen.pid
        self._lock = threading.Lock()
        self._job = _job_for(self.pid) if sys.platform == "win32" else None

    @property
    def stdin(self):
        return self.popen.stdin

    @property
    def stdout(self):
        return self.popen.stdout

    @property
    def stderr(self):
        return self.popen.stderr

    def send_line(self, line: str) -> None:
        with self._lock:
            self.popen.stdin.write(line + "\n")
            self.popen.stdin.flush()

    def close_stdin(self) -> None:
        with self._lock, contextlib.suppress(OSError):
            self.popen.stdin.close()

    def wait(self, timeout=None) -> int | None:
        try:
            return self.popen.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def kill_tree(self) -> None:
        """Kill the child and everything it started. Safe to call more than once."""
        if sys.platform == "win32":
            if self._job is not None and _terminate_job(self._job):
                return
            if self.popen.poll() is None:  # no job: kill by process tree instead
                subprocess.run(["taskkill", "/PID", str(self.pid), "/T", "/F"], capture_output=True,
                               check=False, creationflags=CREATE_NO_WINDOW)
            return
        if self.popen.returncode is None:  # once reaped, the group id could belong to someone else
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.pid, signal.SIGKILL)

    def close(self) -> None:
        """After the child has exited: kill anything it left behind and release the handles."""
        self.kill_tree()
        if self._job is not None:
            with contextlib.suppress(Exception):
                self._job.Close()
            self._job = None
        for stream in (self.popen.stdin, self.popen.stdout, self.popen.stderr):
            with contextlib.suppress(OSError, ValueError):
                stream.close()


def _job_for(pid: int):
    """A Job Object holding this process and its future children, killed when the last handle closes.
    Children may leave it only on purpose (CREATE_BREAKAWAY_FROM_JOB): apps Alfred opens for the user
    do, so stopping or closing Alfred never closes them."""
    try:
        import win32api
        import win32con
        import win32job

        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] |= (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | win32job.JOB_OBJECT_LIMIT_BREAKAWAY_OK
        )
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, pid)
    except Exception:  # noqa: BLE001 - without a job, kill_tree falls back to taskkill /T
        return None
    try:
        win32job.AssignProcessToJobObject(job, handle)
    except Exception:  # noqa: BLE001
        return None
    finally:
        with contextlib.suppress(Exception):
            win32api.CloseHandle(handle)
    return job  # from here on, closing this handle kills the child: keep it until close()


def _terminate_job(job) -> bool:
    try:
        import win32job

        win32job.TerminateJobObject(job, 1)
        return True
    except Exception:  # noqa: BLE001 - the caller falls back to taskkill
        return False
