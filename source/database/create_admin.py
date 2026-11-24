import argparse
import os
import re
import secrets
import string
import sys
from datetime import datetime, timezone

import pymysql
from werkzeug.security import generate_password_hash

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from reset_demo import load_backend_env 


def random_password(length=16):
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw):
            return pw


def main():
    load_backend_env()
    p = argparse.ArgumentParser(description="Create (or reset) a ParkWise administrator")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="Administrator")
    p.add_argument("--reset", action="store_true", help="give an existing admin a new random password")
    p.add_argument("--db", default=os.getenv("MYSQL_DB", "parkwise"))
    args = p.parse_args()

    email = args.email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]{2,}", email):
        sys.exit("Enter a valid email address.")
    password = random_password()
    pw_hash = generate_password_hash(password, method="scrypt")
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)

    conn = pymysql.connect(host=os.getenv("MYSQL_HOST", "127.0.0.1"), port=int(os.getenv("MYSQL_PORT", "3306")),
                           user=os.getenv("MYSQL_USER", "root"), password=os.getenv("MYSQL_PASSWORD", ""),
                           database=args.db, charset="utf8mb4", autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, role FROM Users WHERE email = %s FOR UPDATE", (email,))
            row = cur.fetchone()
            if row and not args.reset:
                sys.exit("This email already has an account. Use --reset to give it a new password.")
            if row:
                if row[1] != "Admin":
                    sys.exit("This account is not an administrator.")
                cur.execute("UPDATE Users SET password_hash = %s, failed_login_count = 0, locked_until = NULL, "
                            "updated_at = %s WHERE user_id = %s", (pw_hash, now, row[0]))
            else:
                cur.execute("INSERT INTO Users (email, password_hash, full_name, role, created_at) "
                            "VALUES (%s, %s, %s, 'Admin', %s)", (email, pw_hash, args.name.strip()[:150], now))
        conn.commit()
    finally:
        conn.close()
    print(f"Administrator {email} is ready.")
    print(f"Password (shown once): {password}")
    print("Log in and change it in Admin > Users (edit your own account).")


if __name__ == "__main__":
    main()
