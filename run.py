"""
Application entrypoint.

Usage (development or Raspberry Pi):
    python run.py

Environment variables:
    APP_HOST      bind address            (default 0.0.0.0, i.e. reachable on the LAN)
    APP_PORT      port                    (default 8080)
    SPOOL_DB      absolute path of the SQLite file (default <project>/spool.db)
    SPOOL_UPLOADS folder for image uploads (default <project>/uploads)
    SEED_DEMO     set to "1" to also load a few sample filaments/models
"""
import os

from app import create_app

# Imported as ``run:app`` by WSGI servers (gunicorn / waitress-serve).
app = create_app()

if __name__ == "__main__":
    host = os.environ.get("APP_HOST", "0.0.0.0")
    port = int(os.environ.get("APP_PORT", "8080"))

    try:
        # waitress is a solid, dependency-light production server for a Pi.
        from waitress import serve

        print(f" * My 3D Workbench is up (waitress)   ->  http://{host}:{port}")
        print(" * Press Ctrl+C to stop.")
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        print(" * waitress not installed — using the Flask dev server.")
        print(f" * My 3D Workbench is up (dev)        ->  http://{host}:{port}")
        app.run(host=host, port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
