"""Core routes: UI index page, uploaded images, health check."""
from flask import Blueprint, current_app, jsonify, render_template, send_from_directory

from .. import db

bp = Blueprint("core", __name__)


@bp.get("/")
def index():
    """Serve the single-page UI."""
    return render_template("index.html")


@bp.get("/api/health")
def health():
    """Liveness probe with row counts (also handy for the UI footer)."""
    return jsonify({
        "status": "ok",
        "filaments": db.query_one("SELECT COUNT(*) n FROM filaments")["n"],
        "models": db.query_one("SELECT COUNT(*) n FROM models")["n"],
    })


@bp.get("/uploads/<path:filename>")
def uploads(filename):
    """Serve user-uploaded pictures."""
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)
