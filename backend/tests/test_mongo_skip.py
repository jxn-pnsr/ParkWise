"""When MongoDB is off, page reads must not wait on every request."""
from pymongo.errors import ServerSelectionTimeoutError

from services import mongo_read


def test_mongo_outage_is_cached(monkeypatch):
    calls = []

    def boom():
        calls.append(1)
        raise ServerSelectionTimeoutError("off")

    monkeypatch.setattr(mongo_read, "get_mongo", boom)
    monkeypatch.setattr(mongo_read, "SKIP_SECONDS", 30)
    mongo_read.reset_skip()
    try:
        assert mongo_read.unread_notifications(1) == 0          # tries once, fails
        assert mongo_read.preferences(1)["theme"] == "light"    # skipped: defaults
        assert mongo_read.unread_notifications(1) == 0          # skipped
        assert len(calls) == 1
    finally:
        mongo_read.reset_skip()
