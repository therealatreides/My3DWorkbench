"""
Application entrypoint for a dev checkout / Raspberry Pi install.

Usage:
    python run.py

(Or, when installed from PyPI:  `my3dworkbench` — same server, same flags.)

The ``app`` object below is exposed as ``run:app`` for WSGI deployment
(``waitress-serve`` / ``gunicorn``).

Environment variables:
    MY3DWORKBENCH_HOST    bind address  (default 0.0.0.0, i.e. reachable on the LAN)
    MY3DWORKBENCH_PORT    port          (default 8080)
    MY3DWORKBENCH_HOME    data folder   (default: the OS app-data dir for My3DWorkbench)
    MY3DWORKBENCH_DB      absolute path of the SQLite file
                          (default <data dir>/My3DWorkbench.db)
    MY3DWORKBENCH_UPLOADS folder for image uploads (default <data dir>/uploads)
    SEED_DEMO             set to "1" to also load a few sample filaments/models
"""
from my3d_workbench import create_app
from my3d_workbench.cli import main

# Imported as ``run:app`` by WSGI servers (gunicorn / waitress-serve).
app = create_app()

if __name__ == "__main__":
    main(app)
