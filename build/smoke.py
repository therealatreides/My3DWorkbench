"""Smoke test for a bundled/built binary.

Finds the newest My3DWorkbench* file in dist/, starts it with a throwaway
data dir, polls /api/health, then shuts the whole process tree down. Exits
non-zero (printing the captured server output) if the server never answers.
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

DIST_DIR = sys.argv[1] if len(sys.argv) > 1 else "dist"
HOST, PORT = "127.0.0.1", "8213"


def find_binary():
    latest = None
    try:
        for name in os.listdir(DIST_DIR):
            if name.startswith("My3DWorkbench"):
                path = os.path.join(DIST_DIR, name)
                if latest is None or os.path.getmtime(path) > os.path.getmtime(latest):
                    latest = path
    except FileNotFoundError:
        pass
    return latest


def kill_tree(proc):
    """Kill the binary *and its children* — a onefile bundle re-execs
    itself, so a plain terminate() would leave the server holding the
    stdout pipe (and the health-check port) forever."""
    if proc.poll() is None:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        else:
            try:
                import signal
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)   # whole tree
            except Exception:
                proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass


def main():
    binary = find_binary()
    if not binary:
        print(f"smoke: no My3DWorkbench* binary found in {DIST_DIR}")
        return 1

    data_home = tempfile.mkdtemp(prefix="m3wb-smoke-")
    env = dict(os.environ,
               MY3DWORKBENCH_HOME=data_home,
               MY3DWORKBENCH_HOST=HOST,
               MY3DWORKBENCH_PORT=PORT)
    kwargs = {"env": env, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
    if sys.platform != "win32":
        kwargs["start_new_session"] = True      # so killpg(terminate) can reach children
    proc = subprocess.Popen([binary], **kwargs)

    # Drain output asynchronously — never read the pipe to completion while
    # the server may still be alive (classic pipe deadlock).
    chunks = []
    def pump():
        try:
            while True:
                b = proc.stdout.read(4096)
                if not b:
                    break
                chunks.append(b)
        except Exception:
            pass
    threading.Thread(target=pump, daemon=True).start()

    body = None
    for _ in range(80):
        try:
            body = urllib.request.urlopen(f"http://{HOST}:{PORT}/api/health", timeout=1).read()
            break
        except Exception:
            time.sleep(0.5)

    kill_tree(proc)

    if body is None:
        print("smoke: server never answered /api/health; output:\n"
              + b"".join(chunks).decode(errors="replace")[-3000:])
        return 1
    print("smoke OK:", body.decode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
