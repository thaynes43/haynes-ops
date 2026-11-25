"""Run the render app as PID 1, forwarding termination and reaping children."""

import os
import signal
import subprocess
import sys


def main():
    child = None
    pending = []

    def forward(signum, _frame=None):
        if child is None:
            pending.append(signum)
            return
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, forward)
    child = subprocess.Popen(sys.argv[1:], start_new_session=True)
    for signum in pending:
        forward(signum)
    while True:
        pid, status = os.wait()
        if pid == child.pid:
            code = os.waitstatus_to_exitcode(status)
            child.returncode = code
            return code if code >= 0 else 128 - code


if __name__ == "__main__":
    raise SystemExit(main())
