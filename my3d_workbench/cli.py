"""Server launcher (``main``) used by the ``my3dworkbench`` CLI, ``python -m``, and ``run.py``.

Environment variables:
    MY3DWORKBENCH_HOST    bind address   (default 0.0.0.0, i.e. reachable on the LAN)
    MY3DWORKBENCH_PORT    port           (default 8080)
    MY3DWORKBENCH_LOG     file to append all server output to, and detach from
                          the console. Required on hosts without a console
                          (Task Scheduler "run whether logged on or not",
                          launchd, systemd services) where print() alone would
                          crash the process
    SEED_DEMO             set to "1" to also load a few sample filaments/models
"""
import os
import sys


def _route_output_to_log():
    """If MY3DWORKBENCH_LOG is set, all server output goes to that file.

    A scheduled/autostarted server has no console attached; writing to
    stdout in that state raises an exception and the first print would
    take the process down before it serves a single request.
    """
    path = os.environ.get("MY3DWORKBENCH_LOG")
    if not path:
        return
    try:
        # line-buffered (buffering=1): every startup line must be visible in
        # the log immediately — an unflushed buffer is silently lost when a
        # service manager or Ctrl+C takes the process down
        handle = open(path, "a", encoding="utf-8", buffering=1, newline="\n")
    except OSError as err:   # unwritable dir, etc. — fail loudly, don't half-run
        print(f"FATAL: cannot open MY3DWORKBENCH_LOG {path!r}: {err}", file=sys.stderr)
        raise SystemExit(1)
    handle.write("— My 3D Workbench starting (log enabled) —\n")
    handle.flush()
    sys.stdout = handle
    sys.stderr = handle


def main(app=None):
    host = os.environ.get("MY3DWORKBENCH_HOST", "0.0.0.0")
    port = int(os.environ.get("MY3DWORKBENCH_PORT", "8080"))
    _route_output_to_log()

    from . import create_app

    if app is None:
        app = create_app()

    try:
        # waitress is a solid, dependency-light production server (also good on a Pi).
        from waitress import serve

        print(f" * My 3D Workbench is up (waitress)   ->  http://{host}:{port}")
        print(" * Press Ctrl+C to stop (or use your service manager to stop it).")
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        print(" * waitress not installed — using the Flask dev server.")
        print(f" * My 3D Workbench is up (dev)        ->  http://{host}:{port}")
        app.run(host=host, port=port, debug=os.environ.get("FLASK_DEBUG") == "1")


if __name__ == "__main__":
    main()
