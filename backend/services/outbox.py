"""MySQL outbox for MongoDB sync (spec 10.2).

Events are saved in the same transaction and synced later by the worker.

Payload:
    entity_type, entity_id   changed record
    actor_user_id, role, ip  who made the change
    metadata                 extra details
    target_user_id, notify   notification details
    session                  parking session data
"""
import json
import uuid

from core.auth import actor
from core.timeutil import now_utc


def write_event(cur, event_type, entity_type, entity_id, metadata=None,
                notify=None, target_user_id=None, session=None, who=None):
    payload = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        **(who or actor()),
        "metadata": metadata or {},
    }
    if notify:
        payload["notify"] = notify
        payload["target_user_id"] = target_user_id
    if session:
        payload["session"] = session
    event_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO Integration_Outbox (event_id, event_type, aggregate_id, payload, status, created_at) "
        "VALUES (%s, %s, %s, %s, 'Pending', %s)",
        (event_id, event_type, None if entity_id is None else str(entity_id),
         json.dumps(payload, default=str, ensure_ascii=False), now_utc()))
    return event_id


def notification(kind, title, message):
    return {"type": kind, "title": title, "message": message}


SYSTEM = {"actor_user_id": None, "actor_role": "System", "ip_address": None}