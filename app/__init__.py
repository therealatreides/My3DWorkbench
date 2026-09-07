"""My 3D Workbench — Flask application factory (all wiring lives here)."""
import os

from flask import Flask

from . import db as database
from .errors import APIError
from .routes import core, filaments, models, printers, settings


def create_app(config_overrides=None):
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    app = Flask(
        __name__,
        template_folder=os.path.join(base_dir, "templates"),
        static_folder=os.path.join(base_dir, "static"),
        static_url_path="/static",
    )
    app.config.update(
        DATABASE_PATH=os.path.join(base_dir, "spool.db"),
        UPLOAD_FOLDER=os.path.join(base_dir, "uploads"),
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,   # 10 MB upload cap
    )
    app.config["DATABASE_PATH"] = os.environ.get("SPOOL_DB", app.config["DATABASE_PATH"])
    app.config["UPLOAD_FOLDER"] = os.environ.get("SPOOL_UPLOADS", app.config["UPLOAD_FOLDER"])
    if config_overrides:
        app.config.update(config_overrides)

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
