"""User accounts, kept in ``<data_dir>/users.json``.

New users create an account on the page with the shared sign-up code, or an
operator creates one with ``medcomms users add``. Passwords are stored only
as salted scrypt hashes. Sign-in cookies are signed with a key kept in
``<data_dir>/secret.key``; resetting a password or disabling an account
invalidates that account's cookies.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
MIN_PASSWORD = 8
COOKIE_DAYS = 30
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


class AccountError(ValueError):
    """A problem to show to the person signing up or in."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return (
        "scrypt$"
        + base64.b64encode(salt).decode()
        + "$"
        + base64.b64encode(digest).decode()
    )


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), **_SCRYPT
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def generate_password() -> str:
    return secrets.token_urlsafe(9)  # 12 characters


class UserStore:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "users.json"
        self._secret = self._load_secret()

    # Storage --------------------------------------------------------------

    def _load_secret(self) -> bytes:
        path = self.root / "secret.key"
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_hex(32))
        return bytes.fromhex(path.read_text().strip())

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}

    def _write(self, users: dict):
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(users, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    # Accounts -------------------------------------------------------------

    def all(self) -> dict:
        return self._read()

    def get(self, name: str) -> dict | None:
        return self._read().get(name)

    def create(self, name: str, password: str) -> None:
        name = name.strip().lower()
        if not USERNAME.match(name):
            raise AccountError(
                "Usernames are 3–32 characters: lowercase letters, digits, "
                "'.', '_' or '-'."
            )
        if len(password) < MIN_PASSWORD:
            raise AccountError(f"Passwords need at least {MIN_PASSWORD} characters.")
        users = self._read()
        if name in users:
            raise AccountError("That username is taken.")
        users[name] = {
            "password": hash_password(password),
            "created": _now(),
            "last_seen": None,
            "disabled": False,
        }
        self._write(users)

    def update(self, name: str, **fields) -> bool:
        users = self._read()
        if name not in users:
            return False
        users[name].update(fields)
        self._write(users)
        return True

    def set_password(self, name: str, password: str) -> bool:
        return self.update(name, password=hash_password(password))

    def remove(self, name: str) -> bool:
        users = self._read()
        if users.pop(name, None) is None:
            return False
        self._write(users)
        return True

    def authenticate(self, name: str, password: str) -> str:
        """Return the username if the password is right; raise AccountError."""
        name = name.strip().lower()
        user = self.get(name)
        if not user or not check_password(password, user["password"]):
            raise AccountError("Wrong username or password.")
        if user.get("disabled"):
            raise AccountError("This account is disabled.")
        return name

    def touch(self, name: str):
        self.update(name, last_seen=_now())

    # Cookies --------------------------------------------------------------

    def _signature(self, name: str, expires: int, password_hash: str) -> str:
        message = f"{name}:{expires}:{password_hash}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def cookie_for(self, name: str) -> str:
        expires = int(time.time()) + COOKIE_DAYS * 86400
        user = self.get(name)
        return f"{name}:{expires}:{self._signature(name, expires, user['password'])}"

    def user_from_cookie(self, cookie: str) -> str | None:
        try:
            name, expires, signature = cookie.rsplit(":", 2)
            expires = int(expires)
        except ValueError:
            return None
        user = self.get(name)
        if not user or user.get("disabled") or expires < time.time():
            return None
        expected = self._signature(name, expires, user["password"])
        return name if hmac.compare_digest(signature, expected) else None
