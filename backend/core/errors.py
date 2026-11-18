"""Errors the API can return, and the handlers that turn them into JSON.

Services raise these; routes never build error responses by hand.

    raise Conflict("SLOT_TAKEN", "This bay is already booked for that time.")
    -> 409 {"ok": false, "error": {"code": "SLOT_TAKEN", "message": "..."}}
"""
import logging

from flask import request
from werkzeug.exceptions import HTTPException

from core.responses import fail

log = logging.getLogger(__name__)


class AppError(Exception):
    status = 400
    code = "BAD_REQUEST"
    message = "The request is not valid."

    def __init__(self, code=None, message=None, fields=None, extra=None):
        super().__init__(message or self.message)
        self.code = code or self.code
        self.message = message or self.message
        self.fields = fields      # {"field": "why"} for form errors
        self.extra = extra        # any extra data for the frontend (e.g. alternative slots)


class ValidationError(AppError):
    status = 400
    code = "VALIDATION_ERROR"
    message = "Please check the highlighted fields."


class Unauthenticated(AppError):
    status = 401
    code = "UNAUTHENTICATED"
    message = "Please log in."


class Forbidden(AppError):
    status = 403
    code = "FORBIDDEN"
    message = "You do not have permission to do this."


class NotFound(AppError):
    status = 404
    code = "NOT_FOUND"
    message = "Not found."


class Conflict(AppError):
    """Breaks a business rule or the current state (always pass a specific code)."""
    status = 409
    code = "CONFLICT"
    message = "This action is not allowed right now."


class Locked(AppError):
    status = 423
    code = "ACCOUNT_LOCKED"
    message = "This account is temporarily locked."


class ServiceUnavailable(AppError):
    status = 503
    code = "SERVICE_UNAVAILABLE"
    message = "Part of the system is unavailable. Please try again."


_HTTP_CODES = {
    400: ("BAD_REQUEST", "The request is not valid."),
    401: ("UNAUTHENTICATED", "Please log in."),
    403: ("FORBIDDEN", "You do not have permission to do this."),
    404: ("NOT_FOUND", "Not found."),
    405: ("METHOD_NOT_ALLOWED", "This method is not allowed here."),
    413: ("PAYLOAD_TOO_LARGE", "The request is too large."),
    415: ("UNSUPPORTED_MEDIA_TYPE", "Send the data as JSON."),
}


def register_error_handlers(app):
    @app.errorhandler(AppError)
    def _app_error(e):
        if e.status >= 500:
            log.warning("AppError %s: %s", e.code, e.message)
        return fail(e.code, e.message, e.status, fields=e.fields, extra=e.extra)

    @app.errorhandler(HTTPException)
    def _http_error(e):
        # Pages outside /api (static frontend files) keep Flask's normal HTML errors.
        if not request.path.startswith("/api"):
            return e
        code, message = _HTTP_CODES.get(e.code, ("HTTP_%s" % e.code, e.description or "Something went wrong."))
        return fail(code, message, e.code)

    @app.errorhandler(Exception)
    def _unexpected(e):
        # Any transaction in progress has already been rolled back by db.mysql.transaction().
        log.exception("Unhandled error on %s %s", request.method, request.path)
        return fail("INTERNAL_ERROR", "Something went wrong on the server. Please try again.", 500)
