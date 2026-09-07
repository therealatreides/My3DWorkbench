"""Shared application error type.

Routes raise :class:`APIError` for any expected failure (validation, 404,
conflict…). :func:`app.create_app` registers a single handler that renders it
as a consistent JSON payload: ``{"error": "<message>"}`` with the proper
HTTP status code.
"""


class APIError(Exception):
    """An expected, user-facing API error.

    ``details`` is an optional dict of extra JSON fields merged into the
    response body (used e.g. by the CSV import to return per-row skip
    reasons alongside the 400 error message).
    """

    def __init__(self, message, status=400, details=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.details = details if isinstance(details, dict) else {}
