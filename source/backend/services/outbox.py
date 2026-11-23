"""Integration_Outbox: the MySQL -> MongoDB sync queue (spec 10.2).

Every SQL transaction that changes something important calls write_event()
with the SAME cursor, so the event is saved only if the transaction commits.
The worker (phase 2) later reads Pending events and writes them to MongoDB.

Payload format (must match database/seed_mongo.py apply_event):
    entity_type, entity_id            what changed
    actor_user_id, actor_role, ip_address
    metadata                          free-form details
    target_user_id + notify           -> a Notification for that user
        notify = {type, title, message}
    session                           -> Parking_Sessions (check-in/out)
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
