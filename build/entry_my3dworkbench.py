"""PyInstaller entry point.

A single import chain — ``my3d_workbench.cli`` (the server launcher) — so
the bundler follows every module the app uses (Flask, waitress, routes, db,
cost engine) via normal imports. Note: package ``__main__`` submodules are
not reliably collected by PyInstaller, which is why the launcher lives in
``cli.py``. Templates and static files are added as data by the build
command (see .github/workflows/release.yml).
"""
import sys

from my3d_workbench.cli import main

if __name__ == "__main__":
    main()
    sys.exit(0)
