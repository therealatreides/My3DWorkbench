"""My 3D Workbench — Flask application factory (all wiring lives here)."""
import os
import sys

from flask import Flask

from . import db as database
from .errors import APIError
from .routes import core, filaments, models, printers, settings

#: Per-user data dir suffix, e.g. ``%LOCALAPPDATA%\\My3DWorkbench``.
APP_NAME = "My3DWorkbench"


def default_data_dir():
    """Where user data (``My3DWorkbench.db``, ``uploads/``) lives by default.

    Override with ``MY3DWORKBENCH_HOME`` (any folder). On Windows the dir is
    under ``%LOCALAPPDATA%``, on macOS under ``~/Library/Application Support``,
    and on Linux under ``$XDG_DATA_HOME`` (or ``~/.local/share``) — so a dev
    checkout, a pip install, and a bundled binary all share one profile.
    """
    home = os.environ.get("MY3DWORKBENCH_HOME")
    if home:
        return os.path.abspath(home)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, APP_NAME)


def create_app(config_overrides=None):
    pkg_dir = os.path.dirname(__file__)
    app = Flask(
        __name__,
        template_folder=os.path.join(pkg_dir, "templates"),
        static_folder=os.path.join(pkg_dir, "static"),
        static_url_path="/static",
    )
    data_dir = default_data_dir()
    app.config.update(
        DATA_DIR=data_dir,
        DATABASE_PATH=os.path.join(data_dir, "My3DWorkbench.db"),
        UPLOAD_FOLDER=os.path.join(data_dir, "uploads"),
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,   # 10 MB upload cap
    )
    app.config["DATABASE_PATH"] = os.environ.get("MY3DWORKBENCH_DB", app.config["DATABASE_PATH"])
    app.config["UPLOAD_FOLDER"] = os.environ.get("MY3DWORKBENCH_UPLOADS", app.config["UPLOAD_FOLDER"])
    if config_overrides:
        app.config.update(config_overrides)

    os.makedirs(app.config["DATA_DIR"], exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    database.init_app(app)
    if os.environ.get("SEED_DEMO") == "1":
        from .seed_demo import seed_demo
        seed_demo(app)

    app.teardown_appcontext(database.close_db)

    app.register_blueprint(core.bp)         # /, /uploads/*, /api/health
    app.register_blueprint(filaments.bp)    # /api/filaments
    app.register_blueprint(models.bp)       # /api/models
    app.register_blueprint(printers.bp)     # /api/printers
    app.register_blueprint(settings.bp)     # /api/settings

    @app.errorhandler(APIError)
    def _api_error(err):
        body = {"error": err.message}
        if getattr(err, "details", None):
            body.update(err.details)
        return body, err.status

    @app.errorhandler(404)
    def _e404(_):
        return {"error": "Not found."}, 404

    @app.errorhandler(405)
    def _e405(_):
        return {"error": "Method not allowed."}, 405

    @app.errorhandler(413)
    def _e413(_):
        return {"error": "Upload too large (max 10 MB)."}, 413

    # Unexpected server-side failures must keep the same JSON envelope as every
    # other API error, never an HTML error page.
    @app.errorhandler(500)
    def _e500(_):
        return {"error": "Internal server error."}, 500

    return app
