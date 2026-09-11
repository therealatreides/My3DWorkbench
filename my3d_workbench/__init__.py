"""My 3D Workbench — Flask application factory (all wiring lives here)."""
import os
import sys

from flask import Flask

from . import db as database
from .errors import APIError
from .routes import core, filaments, models, printers, settings

#: Product name (suffix of the data files: ``My3DWorkbench.db``, ``uploads/``).
APP_NAME = "My3DWorkbench"


def default_data_dir():
    """Where user data (``My3DWorkbench.db``, ``uploads/``) lives by default.

    Local to the install on purpose: a dev checkout keeps its database in
    the repo next to this package, and a bundled (PyInstaller) binary keeps
    it next to its executable -- the data lives where the app lives, never
    in the OS app-data folder by default.

    Point it anywhere else with ``MY3DWORKBENCH_HOME`` (any folder), or pin
    the exact files with ``MY3DWORKBENCH_DB`` / ``MY3DWORKBENCH_UPLOADS``.
    """
    home = os.environ.get("MY3DWORKBENCH_HOME")
    if home:
        return os.path.abspath(os.path.expanduser(home))
    if getattr(sys, "frozen", False):      # PyInstaller bundle: __file__ is virtual
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


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
