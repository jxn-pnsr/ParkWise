import argparse
import os
import re
import sys

import pymysql
from pymysql.constants import CLIENT

HERE = os.path.dirname(os.path.abspath(__file__))


def load_backend_env():
    """Use backend/.env so the team only types the password once."""
    path = os.path.join(HERE, "..", "backend", ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), _clean_value(value))


def _clean_value(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def confirm(db_name, with_mongo):
    """Ask before deleting everything. Returns True to go on."""
    what = f"MySQL database `{db_name}`" + (" and the MongoDB collections" if with_mongo else "")
    print(f"This DELETES all data in {what} and loads the demo data again.")
    if not sys.stdin or not sys.stdin.isatty():
        print("Not an interactive terminal: run again with --yes to confirm.")
        return False
    try:
        answer = input(f"Type the database name ({db_name}) to continue: ").strip()
    except EOFError:
        return False
    return answer == db_name


def run_sql_file(conn, path, db_name):
    with open(path, encoding="utf-8") as f:
        sql = f.read()
    # schema.sql creates and uses `parkwise`; point it at the database we want
    sql = re.sub(r"CREATE DATABASE IF NOT EXISTS parkwise\b", f"CREATE DATABASE IF NOT EXISTS `{db_name}`", sql)
    sql = re.sub(r"^USE parkwise;", f"USE `{db_name}`;", sql, flags=re.M)
    with conn.cursor() as cur:
        cur.execute(sql)
        while cur.nextset():  # step through every statement's result
            pass
    conn.commit()


def reset_mysql(db_name):
    if not re.fullmatch(r"[A-Za-z0-9_]+", db_name):
        sys.exit("Database name may only use letters, numbers and _")
    conn = pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        charset="utf8mb4",
        client_flag=CLIENT.MULTI_STATEMENTS,
        autocommit=False)
    try:
        run_sql_file(conn, os.path.join(HERE, "schema.sql"), db_name)
        conn.select_db(db_name)
        run_sql_file(conn, os.path.join(HERE, "seed.sql"), db_name)
        with conn.cursor() as cur:
            cur.execute("SELECT (SELECT COUNT(*) FROM Users), (SELECT COUNT(*) FROM Reservations), "
                        "(SELECT COUNT(*) FROM Integration_Outbox)")
            users, res, events = cur.fetchone()
        print(f"MySQL `{db_name}`: {users} users, {res} reservations, {events} outbox events")
    finally:
        conn.close()

PREFERENCES_RULE = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["userId", "updatedAt"],
    "properties": {
        "userId": {"bsonType": ["int", "long"]},
        "notifications": {"bsonType": "object", "properties": {
            k: {"bsonType": "bool"} for k in ("holdExpired", "noShow", "checkIn", "checkOut", "overstay")}},
        "defaultVehicleId": {"bsonType": ["int", "long", "null"]},
        "updatedAt": {"bsonType": "date"},
    },
}}


def update_preferences_rule(db):
    """Bring the User_Preferences validator up to date (no data is touched)."""
    from pymongo.errors import PyMongoError
    if "User_Preferences" not in db.list_collection_names():
        return
    try:
        db.command("collMod", "User_Preferences", validator=PREFERENCES_RULE, validationLevel="moderate",
                   validationAction="error")
    except PyMongoError as e:
        print(f"Could not update the User_Preferences rule ({e}). Run mongo_init.js once instead.")



def reset_mongo(db_name):
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    sys.path.insert(0, HERE)
    import seed_mongo

    client = MongoClient(os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017"), serverSelectionTimeoutMS=3000)
    try:
        client.admin.command("ping")
    except PyMongoError:
        print("MongoDB is not running: skipped. (Start it, run mongo_init.js once, then run this again.)")
        return
    os.environ["MYSQL_DB"] = db_name
    update_preferences_rule(client[os.getenv("MONGO_DB", "parkwise")])
    counts, n = seed_mongo.seed(client[os.getenv("MONGO_DB", "parkwise")], seed_mongo.mysql_conn())
    print(f"MongoDB: replayed {n} events -> " + ", ".join(f"{k} {v}" for k, v in counts.items()))


if __name__ == "__main__":
    load_backend_env()
    p = argparse.ArgumentParser(description="Rebuild the ParkWise demo data")
    p.add_argument("--db", default=os.getenv("MYSQL_DB", "parkwise"), help="MySQL database name")
    p.add_argument("--skip-mongo", action="store_true", help="only reset MySQL")
    p.add_argument("--yes", "-y", action="store_true", help="do not ask before deleting the data")
    args = p.parse_args()
    if not args.yes and not confirm(args.db, not args.skip_mongo):
        sys.exit("Cancelled. Nothing was changed.")
    reset_mysql(args.db)
    if not args.skip_mongo:
        reset_mongo(args.db)
