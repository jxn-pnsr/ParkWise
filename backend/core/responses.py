from flask import jsonify, request

DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100


def ok(data=None, status=200):
    return jsonify({"ok": True, "data": data}), status


def created(data=None):
    return ok(data, 201)


def fail(code, message, status=400, fields=None, extra=None):
    error = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    if extra:
        error.update(extra)
    return jsonify({"ok": False, "error": error}), status


def get_pagination(args=None):
    """Return page, per_page, and offset."""
    args = request.args if args is None else args
    try:
        page = int(args.get("page", 1))
        per_page = int(args.get("per_page", DEFAULT_PER_PAGE))
    except (TypeError, ValueError):
        from core.errors import ValidationError
        raise ValidationError(fields={"page": "page and per_page must be numbers."})
    page = max(page, 1)
    per_page = min(max(per_page, 1), MAX_PER_PAGE)
    return page, per_page, (page - 1) * per_page


def paginated(items, page, per_page, total):
    return ok({"items": items, "page": page, "per_page": per_page, "total": total})