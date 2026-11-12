import re

from flask import request

from core.errors import ValidationError
from core.timeutil import parse_client_datetime

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
PHONE_RE = re.compile(r"^0\d{8,9}$")


def request_json():
    data = request.get_json(silent=True)
    if data is None:
        if request.data:
            raise ValidationError("BAD_JSON", "The request body must be valid JSON.")
        return {}
    if not isinstance(data, dict):
        raise ValidationError("BAD_JSON", "The request body must be a JSON object.")
    return data


class Input:
    def __init__(self, data):
        self.data = data or {}
        self.errors = {}

    def has(self, name):
        return name in self.data and self.data[name] is not None

    def _missing(self, name, required, label):
        if required:
            self.errors[name] = f"{label or 'This field'} is required."
        return None

    def text(self, name, required=False, max_len=255, min_len=0, label=None, pattern=None, pattern_msg=None):
        value = self.data.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            return self._missing(name, required, label)
        if not isinstance(value, str):
            self.errors[name] = "Must be text."
            return None
        value = re.sub(r"\s+", " ", value.strip())
        if len(value) < min_len:
            self.errors[name] = f"Must be at least {min_len} characters."
        elif len(value) > max_len:
            self.errors[name] = f"Must be at most {max_len} characters."
        elif pattern and not pattern.match(value):
            self.errors[name] = pattern_msg or "Format is not valid."
        return value

    def email(self, name="email", required=True):
        value = self.text(name, required=required, max_len=255, label="Email")
        if value and not EMAIL_RE.match(value):
            self.errors[name] = "Enter a valid email address."
        return value.lower() if value else value

    def phone(self, name="phone", required=False):
        value = self.data.get(name)
        if isinstance(value, str):
            value = re.sub(r"[\s-]", "", value)
            self.data[name] = value
        return self.text(name, required=required, max_len=20, label="Phone",
                         pattern=PHONE_RE, pattern_msg="Use 9-10 digits starting with 0.")

    def password(self, name="password", required=True, min_len=8):
        value = self.data.get(name)
        if not value:
            return self._missing(name, required, "Password")
        if not isinstance(value, str):
            self.errors[name] = "Must be text."
            return None
        if len(value) < min_len:
            self.errors[name] = f"Use at least {min_len} characters."
        elif len(value) > 128:
            self.errors[name] = "Use at most 128 characters."
        return value

    def integer(self, name, required=False, min_value=None, max_value=None, label=None):
        value = self.data.get(name)
        if value is None or value == "":
            return self._missing(name, required, label)
        if isinstance(value, bool):
            self.errors[name] = "Must be a whole number."
            return None
        try:
            number = int(value)
            if isinstance(value, float) and not value.is_integer():
                raise ValueError
        except (TypeError, ValueError):
            self.errors[name] = "Must be a whole number."
            return None
        if min_value is not None and number < min_value:
            self.errors[name] = f"Must be at least {min_value}."
        elif max_value is not None and number > max_value:
            self.errors[name] = f"Must be at most {max_value}."
        return number

    def boolean(self, name, required=False):
        value = self.data.get(name)
        if value is None:
            return self._missing(name, required, None)
        if isinstance(value, bool):
            return value
        self.errors[name] = "Must be true or false."
        return None

    def choice(self, name, options, required=False, label=None):
        value = self.data.get(name)
        if value is None or value == "":
            return self._missing(name, required, label)
        if value not in options:
            self.errors[name] = "Choose one of: " + ", ".join(options) + "."
            return None
        return value

    def datetime(self, name, required=False, label=None):
        value = self.data.get(name)
        if value is None or value == "":
            return self._missing(name, required, label)
        try:
            return parse_client_datetime(value)
        except (TypeError, ValueError):
            self.errors[name] = "Use a date and time like 2026-11-20T10:00:00+07:00."
            return None

    def error(self, name, message):
        self.errors[name] = message

    def done(self):
        if self.errors:
            raise ValidationError(fields=self.errors)


def query_int(name, default=None, min_value=None, max_value=None):
    """Read an integer from the query string."""
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValidationError(fields={name: "Must be a whole number."})
    if (min_value is not None and value < min_value) or (max_value is not None and value > max_value):
        raise ValidationError(fields={name: "Value is out of range."})
    return value