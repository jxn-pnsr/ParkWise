"""Request validation. Collects field errors before raising ValidationError.

    v = Input(request_json())
    name = v.text("full_name", required=True, max_len=150)
    hours = v.integer("duration_hours", required=True, min_value=1, max_value=12)
    v.done()
"""
import json
import math
import re

from flask import request

from core.errors import ValidationError
from core.timeutil import parse_client_datetime

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
PHONE_RE = re.compile(r"^0\d{8,9}$")

# Reject oversized integers before arithmetic.
MAX_INT = 10 ** 12

# Common passwords rejected by the strength check.
COMMON_PASSWORDS = {
    "password", "password1", "password123", "passw0rd", "12345678", "123456789", "1234567890",
    "qwertyui", "qwerty123", "11111111", "00000000", "abcd1234", "iloveyou", "admin123",
    "letmein1", "welcome1", "aaaaaaaa", "asdfghjk", "zxcvbnm1", "baseball", "football",
}


def _reject_constant(name):
    raise ValueError(f"{name} is not allowed")


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("number out of range")
    return value


def _has_lone_surrogate(value):
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return True
        return False
    if isinstance(value, dict):
        return any(_has_lone_surrogate(k) or _has_lone_surrogate(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_lone_surrogate(v) for v in value)
    return False


def request_json():
    """Parse a valid JSON object; reject invalid numbers and Unicode (M13)."""
    raw = request.get_data(cache=True)
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw, parse_constant=_reject_constant, parse_float=_finite_float)
    except (ValueError, RecursionError):
        raise ValidationError("BAD_JSON", "The request body must be valid JSON.")
    if not isinstance(data, dict):
        raise ValidationError("BAD_JSON", "The request body must be a JSON object.")
    if _has_lone_surrogate(data):
        raise ValidationError("BAD_JSON", "The request contains characters that are not valid text.")
    return data


def like_escape(text):
    """Escape LIKE wildcards for literal matching (M10)."""
    return (text or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_contains(text):
    return "%" + like_escape(text) + "%"


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

    def password(self, name="password", required=True, min_len=8, strong=None):
        """Validate password length and strength; login may use min_len=1."""
        value = self.data.get(name)
        if not value:
            return self._missing(name, required, "Password")
        if not isinstance(value, str):
            self.errors[name] = "Must be text."
            return None
        strong = min_len >= 8 if strong is None else strong
        if len(value) < min_len:
            self.errors[name] = f"Use at least {min_len} characters."
        elif len(value) > 128:
            self.errors[name] = "Use at most 128 characters."
        elif strong and (not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value)):
            self.errors[name] = "Use at least one letter and one number."
        elif strong and (value.lower() in COMMON_PASSWORDS or len(set(value)) < 4):
            self.errors[name] = "This password is too easy to guess. Choose another one."
        return value

    def integer(self, name, required=False, min_value=None, max_value=None, label=None):
        value = self.data.get(name)
        if value is None or value == "":
            return self._missing(name, required, label)
        if isinstance(value, bool):
            self.errors[name] = "Must be a whole number."
            return None
        try:
            if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
                raise ValueError
            if isinstance(value, str) and len(value.strip()) > 15:
                raise ValueError
            number = int(value)
            if abs(number) > MAX_INT:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
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
        except (TypeError, ValueError, OverflowError):
            self.errors[name] = "Use a date and time like 2026-11-20T10:00:00+07:00."
            return None

    def error(self, name, message):
        self.errors[name] = message

    def done(self):
        if self.errors:
            raise ValidationError(fields=self.errors)


def query_int(name, default=None, min_value=None, max_value=None):
    """Read and validate an integer query parameter."""
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        if len(raw) > 15:
            raise ValueError
        value = int(raw)
    except ValueError:
        raise ValidationError(fields={name: "Must be a whole number."})
    if (min_value is not None and value < min_value) or (max_value is not None and value > max_value):
        raise ValidationError(fields={name: "Value is out of range."})
    return value