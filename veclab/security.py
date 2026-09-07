"""Password hashing and revocable database-backed local sessions."""
from dataclasses import dataclass
from typing import Any
import base64
import hashlib
import hmac
import re
import secrets
import time

from .database import Database, utc_now

ITERATIONS = 600_000
COOKIE_NAME = "veclab_session"


class AccessError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def validate_password(password: str) -> None:
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise ValueError("口令长度必须为12至128个字符")
    if password.isspace():
        raise ValueError("口令不能全部为空白字符")


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    value = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, ITERATIONS
    )
    encode = lambda item: base64.b64encode(item).decode("ascii")
    return f"pbkdf2_sha256${ITERATIONS}${encode(salt)}${encode(value)}"


def verify_password(password: str, stored: str) -> bool:
    if not isinstance(password, str) or len(password) > 128:
        return False
    try:
        name, rounds, salt, expected = stored.split("$")
        if name != "pbkdf2_sha256" or int(rounds) != ITERATIONS:
            return False
        value = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            base64.b64decode(salt, validate=True),
            int(rounds),
        )
        return hmac.compare_digest(
            value, base64.b64decode(expected, validate=True)
        )
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Identity:
    username: str
    csrf: str
    session_hash: str
    expires_at: float


class Auth:
    def __init__(self, db: Database, lifetime: int = 28800):
        self.db = db
        self.lifetime = lifetime
        self._dummy_hash = hash_password(secrets.token_urlsafe(24))

    def setup(self, username: str, password: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
            raise ValueError("用户名应为3至32位字母、数字或_.-")
        encoded = hash_password(password)
        now = utc_now()
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise ValueError("管理员已初始化，禁止重复初始化")
            conn.execute(
                "INSERT INTO users VALUES (?,?,?,?)",
                (username, encoded, now, now),
            )
            self.db.audit(conn, username, "auth.setup", username, {})

    def is_initialized(self) -> bool:
        return self.db.one("SELECT username FROM users LIMIT 1") is not None

    def login(self, username: str, password: str) -> tuple[str, Identity]:
        now = time.time()
        subject = username.casefold()[:64]
        locked = False
        invalid = False
        token = ""
        identity = None
        with self.db.transaction() as conn:
            attempt = conn.execute(
                "SELECT * FROM login_attempts WHERE subject=?", (subject,)
            ).fetchone()
            if attempt and attempt["locked_until"] > now:
                locked = True
            else:
                row = conn.execute(
                    "SELECT * FROM users WHERE username=?", (username,)
                ).fetchone()
                expected = row["password_hash"] if row else self._dummy_hash
                valid = verify_password(password, expected)
                if not row or not valid:
                    invalid = True
                    count = (attempt["failures"] if attempt else 0) + 1
                    lock_until = now + 60 if count >= 5 else 0
                    conn.execute(
                        "INSERT INTO login_attempts VALUES (?,?,?) "
                        "ON CONFLICT(subject) DO UPDATE SET "
                        "failures=excluded.failures,"
                        "locked_until=excluded.locked_until",
                        (subject, 0 if count >= 5 else count, lock_until),
                    )
                    self.db.audit(
                        conn, "anonymous", "auth.failed", "login", {}
                    )
                else:
                    conn.execute(
                        "DELETE FROM login_attempts WHERE subject=?",
                        (subject,),
                    )
                    conn.execute(
                        "DELETE FROM sessions WHERE expires_at<?", (now,)
                    )
                    token = secrets.token_urlsafe(32)
                    csrf = secrets.token_urlsafe(32)
                    hashed = token_hash(token)
                    expiry = now + self.lifetime
                    conn.execute(
                        "INSERT INTO sessions VALUES (?,?,?,?,?)",
                        (hashed, username, csrf, expiry, utc_now()),
                    )
                    self.db.audit(conn, username, "auth.login", username, {})
                    identity = Identity(username, csrf, hashed, expiry)
        if locked:
            raise AccessError("登录失败过多，请60秒后重试", 429)
        if invalid or identity is None:
            raise AccessError("用户名或口令不正确")
        return token, identity

    def authenticate(self, token: str | None) -> Identity:
        if not token or len(token) > 128:
            raise AccessError("请先登录")
        row = self.db.one(
            "SELECT * FROM sessions WHERE token_hash=?", (token_hash(token),)
        )
        if not row or row["expires_at"] <= time.time():
            raise AccessError("会话已失效，请重新登录")
        return Identity(
            row["username"], row["csrf"], row["token_hash"], row["expires_at"]
        )

    @staticmethod
    def check_csrf(identity: Identity, provided: str | None) -> None:
        if not provided or not hmac.compare_digest(identity.csrf, provided):
            raise AccessError("请求校验失败，请刷新页面后重试", 403)

    def logout(self, identity: Identity) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE token_hash=?",
                (identity.session_hash,),
            )
            self.db.audit(
                conn, identity.username, "auth.logout", identity.username, {}
            )

    def change_password(
        self, identity: Identity, old: str, new: str
    ) -> None:
        validate_password(new)
        row = self.db.one(
            "SELECT password_hash FROM users WHERE username=?",
            (identity.username,),
        )
        if not row or not verify_password(old, row["password_hash"]):
            raise AccessError("当前口令不正确", 403)
        if old == new:
            raise ValueError("新口令不能与原口令相同")
        encoded = hash_password(new)
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE users SET password_hash=?,updated_at=? "
                "WHERE username=?",
                (encoded, utc_now(), identity.username),
            )
            conn.execute(
                "DELETE FROM sessions WHERE username=?", (identity.username,)
            )
            self.db.audit(
                conn, identity.username, "auth.password", identity.username,
                {"sessions_revoked": True},
            )

    @staticmethod
    def public_identity(identity: Identity) -> dict[str, Any]:
        return {
            "username": identity.username,
            "csrf": identity.csrf,
            "expires_at": identity.expires_at,
        }
