from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.paths import DATA_DIR, validate_id


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_SCRYPT = (2**14, 8, 1)
_PBKDF2_ITERATIONS = 310_000


class UserStoreError(ValueError):
    def __init__(self, message: str, *, code: str = "account_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AuthIdentity:
    user_id: str
    session_id: str | None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _email(value: str) -> str:
    normalized = (value or "").strip().lower()
    if len(normalized) > 254 or not _EMAIL_RE.fullmatch(normalized):
        raise UserStoreError("请输入有效的邮箱地址", code="invalid_email")
    return normalized


def _password(value: str) -> str:
    if not 8 <= len(value or "") <= 128:
        raise UserStoreError("密码长度需为 8–128 位", code="weak_password")
    kinds = sum((any(c.isalpha() for c in value), any(c.isdigit() for c in value), any(not c.isalnum() for c in value)))
    if kinds < 2:
        raise UserStoreError("密码至少包含字母、数字和特殊字符中的两种", code="weak_password")
    return value


def _password_hash(value: str) -> str:
    salt = secrets.token_bytes(16)
    if hasattr(hashlib, "scrypt"):
        n, r, p = _SCRYPT
        digest = hashlib.scrypt(value.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
        return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"
    digest = hashlib.pbkdf2_hmac("sha256", value.encode(), salt, _PBKDF2_ITERATIONS, dklen=32)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def _password_matches(value: str, encoded: str | None) -> bool:
    try:
        parts = (encoded or "").split("$")
        algorithm = parts[0]
        if algorithm == "scrypt":
            _, n, r, p, salt, expected = parts
            if not hasattr(hashlib, "scrypt"):
                return False
            actual = hashlib.scrypt(
                value.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                dklen=len(bytes.fromhex(expected)),
            )
        elif algorithm == "pbkdf2_sha256":
            _, iterations, salt, expected = parts
            actual = hashlib.pbkdf2_hmac(
                "sha256", value.encode(), bytes.fromhex(salt), int(iterations),
                dklen=len(bytes.fromhex(expected)),
            )
        else:
            return False
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def _guest_message_limit() -> int:
    try:
        return max(0, int(os.environ.get("AGENT_GUEST_MESSAGE_LIMIT") or 3))
    except ValueError:
        return 3


class UserStore:
    """Accounts and independently revocable device sessions.

    Existing token-only databases migrate in place. Plain passwords, access
    tokens and verification codes are never persisted.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DATA_DIR / "users.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS guest_daily_usage (day TEXT PRIMARY KEY, turn_count INTEGER NOT NULL DEFAULT 0)")
            db.execute("""CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)""")
            existing = {str(row[1]) for row in db.execute("PRAGMA table_info(users)")}
            for name, definition in {
                "email": "TEXT", "password_hash": "TEXT", "display_name": "TEXT NOT NULL DEFAULT ''",
                "email_verified_at": "TEXT", "updated_at": "TEXT", "disabled_at": "TEXT",
                "deleted_at": "TEXT", "account_type": "TEXT NOT NULL DEFAULT 'account'",
                "guest_device_id": "TEXT", "guest_message_count": "INTEGER NOT NULL DEFAULT 0",
            }.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email IS NOT NULL")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_guest_device "
                "ON users(guest_device_id) WHERE guest_device_id IS NOT NULL"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS user_tokens (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE)""")
            db.execute("""CREATE TABLE IF NOT EXISTS user_sessions (
                session_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, user_id TEXT NOT NULL,
                device_id TEXT NOT NULL, device_name TEXT NOT NULL, device_type TEXT NOT NULL,
                platform TEXT NOT NULL, app_version TEXT NOT NULL, created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL, revoked_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE)""")
            session_columns = {
                str(row[1]) for row in db.execute("PRAGMA table_info(user_sessions)")
            }
            if "token_hint" not in session_columns:
                db.execute(
                    "ALTER TABLE user_sessions ADD COLUMN token_hint TEXT NOT NULL DEFAULT ''"
                )
            db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions(user_id, revoked_at, last_seen_at DESC)")
            db.execute("""CREATE TABLE IF NOT EXISTS account_codes (
                code_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, purpose TEXT NOT NULL,
                code_hash TEXT NOT NULL, expires_at TEXT NOT NULL, consumed_at TEXT, created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE)""")
            code_columns = {row[1] for row in db.execute("PRAGMA table_info(account_codes)")}
            if "failed_attempts" not in code_columns:
                db.execute("ALTER TABLE account_codes ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0")
            db.execute("CREATE INDEX IF NOT EXISTS idx_codes_user ON account_codes(user_id, purpose, created_at DESC)")
            db.execute("INSERT OR IGNORE INTO user_tokens SELECT token_hash,user_id,created_at FROM users")
            for row in db.execute("""SELECT t.* FROM user_tokens t LEFT JOIN user_sessions s
                    ON s.token_hash=t.token_hash WHERE s.session_id IS NULL""").fetchall():
                db.execute("""INSERT INTO user_sessions
                    (session_id,token_hash,user_id,device_id,device_name,device_type,
                     platform,app_version,created_at,last_seen_at,revoked_at,token_hint)
                    VALUES (?,?,?,?,?,?,?,?,?,?,NULL,'')""", (
                    f"ses_{uuid.uuid4().hex}", row["token_hash"], row["user_id"], "legacy",
                    "旧版客户端", "unknown", "unknown", "", row["created_at"], row["created_at"],
                ))

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _account(row: sqlite3.Row) -> dict[str, Any]:
        is_guest = str(row["account_type"] or "account") == "guest"
        used = int(row["guest_message_count"] or 0)
        return {
            "user_id": str(row["user_id"]), "email": str(row["email"] or ""),
            "display_name": str(row["display_name"] or ""),
            "email_verified": bool(row["email_verified_at"]), "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"] or row["created_at"]),
            "disabled": bool(row["disabled_at"]),
            "is_guest": is_guest,
            "guest_remaining": max(0, _guest_message_limit() - used) if is_guest else None,
        }

    def _new_session(self, db: sqlite3.Connection, user_id: str, **device: str) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        session_id = validate_id(f"ses_{uuid.uuid4().hex}", kind="session_id")
        created = _iso()
        token_hash = self._token_hash(token)
        device_id = (device.get("device_id") or uuid.uuid4().hex)[:128]
        # A physical/app installation has one active credential. Logging in
        # again rotates that device token without creating duplicate rows.
        db.execute(
            "UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND device_id=? AND revoked_at IS NULL",
            (created, user_id, device_id),
        )
        db.execute("""INSERT INTO user_sessions
            (session_id,token_hash,user_id,device_id,device_name,device_type,platform,
             app_version,created_at,last_seen_at,token_hint)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
            session_id, token_hash, user_id, device_id,
            (device.get("device_name") or "未命名设备")[:120], (device.get("device_type") or "unknown")[:32],
            (device.get("platform") or "unknown")[:80], (device.get("app_version") or "")[:40],
            created, created, token[-6:],
        ))
        db.execute("INSERT OR IGNORE INTO user_tokens VALUES (?,?,?)", (token_hash, user_id, created))
        return token, session_id

    def register(self) -> tuple[str, str]:
        """Compatibility path used by the registration-secret pairing API."""
        user_id = validate_id(f"usr_{uuid.uuid4().hex}", kind="user_id")
        bootstrap = secrets.token_urlsafe(32)
        created = _iso()
        with self._connect() as db:
            db.execute("INSERT INTO users(user_id,token_hash,created_at,updated_at) VALUES(?,?,?,?)",
                       (user_id, self._token_hash(bootstrap), created, created))
            token, _ = self._new_session(db, user_id, device_name="已配对客户端", device_type="paired")
        return user_id, token

    def register_account(self, email: str, password: str, *, display_name: str = "",
                         email_verified: bool = True, device: dict[str, str] | None = None,
                         issue_session: bool = True) -> dict[str, Any]:
        email = _email(email)
        _password(password)
        user_id = validate_id(f"usr_{uuid.uuid4().hex}", kind="user_id")
        created = _iso()
        bootstrap = secrets.token_urlsafe(32)
        with self._connect() as db:
            try:
                db.execute("""INSERT INTO users(user_id,token_hash,created_at,email,password_hash,display_name,email_verified_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?)""", (user_id, self._token_hash(bootstrap), created, email,
                    _password_hash(password), (display_name or email.split("@", 1)[0])[:80],
                    created if email_verified else None, created))
            except sqlite3.IntegrityError as exc:
                raise UserStoreError("该邮箱已注册", code="email_exists") from exc
            token = session_id = None
            if email_verified and issue_session:
                token, session_id = self._new_session(db, user_id, **(device or {}))
            account = self.get_account(user_id, db=db)
        return {"account": account, "token": token, "session_id": session_id}

    def guest_session(self, *, device: dict[str, str], token: str = "") -> dict[str, Any]:
        """Return a stable, server-backed guest identity for one app installation."""
        device_id = (device.get("device_id") or "").strip()[:128]
        if not device_id:
            raise UserStoreError("缺少设备标识", code="invalid_device")
        created = _iso()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM users WHERE account_type='guest' AND guest_device_id=? ",
                (device_id,),
            ).fetchone()
            if row is None:
                user_id = validate_id(f"usr_{uuid.uuid4().hex}", kind="user_id")
                bootstrap = secrets.token_urlsafe(32)
                db.execute(
                    """INSERT INTO users(
                        user_id,token_hash,created_at,display_name,updated_at,
                        account_type,guest_device_id,guest_message_count
                    ) VALUES(?,?,?,?,?,'guest',?,0)""",
                    (
                        user_id,
                        self._token_hash(bootstrap),
                        created,
                        "游客",
                        created,
                        device_id,
                    ),
                )
            else:
                user_id = str(row["user_id"])
                session = db.execute(
                    "SELECT 1 FROM user_sessions WHERE user_id=? AND device_id=? "
                    "AND token_hash=? AND revoked_at IS NULL",
                    (user_id, device_id, self._token_hash(token)),
                ).fetchone()
                if not session or row["deleted_at"] or row["disabled_at"]:
                    raise UserStoreError("恢复游客会话需要有效凭据，请登录账号", code="guest_auth_required")
            token, session_id = self._new_session(db, user_id, **device)
            account = self.get_account(user_id, db=db)
        return {"account": account, "token": token, "session_id": session_id}

    def is_guest(self, user_id: str) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT account_type FROM users WHERE user_id=?", (user_id,)).fetchone()
        return bool(row and row[0] == "guest")

    def consume_guest_message(self, user_id: str, *, limit: int = 3, daily_limit: int | None = None) -> int | None:
        """Atomically reserve one guest turn and return the remaining allowance."""
        user_id = validate_id(user_id, kind="user_id")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT account_type,guest_message_count FROM users "
                "WHERE user_id=? AND deleted_at IS NULL",
                (user_id,),
            ).fetchone()
            if row is None:
                # Static/legacy API tokens resolve identities outside users.db.
                # They are full accounts, never anonymous guest sessions.
                return None
            if str(row["account_type"] or "account") != "guest":
                return None
            used = int(row["guest_message_count"] or 0)
            if used >= limit:
                raise UserStoreError(
                    "游客体验次数已用完，请登录后继续",
                    code="guest_quota_exhausted",
                )
            if daily_limit is not None:
                day = _now().date().isoformat()
                db.execute("INSERT OR IGNORE INTO guest_daily_usage(day) VALUES(?)", (day,))
                reserved = db.execute("UPDATE guest_daily_usage SET turn_count=turn_count+1 WHERE day=? AND turn_count<?", (day, max(0, daily_limit)))
                if reserved.rowcount != 1:
                    raise UserStoreError("今日游客体验额度已用完，请登录账号", code="guest_global_quota_exhausted")
            used += 1
            db.execute(
                "UPDATE users SET guest_message_count=?,updated_at=? WHERE user_id=?",
                (used, _iso(), user_id),
            )
            return max(0, limit - used)

    def refund_guest_message(self, user_id: str) -> None:
        user_id = validate_id(user_id, kind="user_id")
        with self._connect() as db:
            db.execute(
                "UPDATE users SET guest_message_count=MAX(0,guest_message_count-1),updated_at=? "
                "WHERE user_id=? AND account_type='guest'",
                (_iso(), user_id),
            )

    def issue_token(self, user_id: str, **device: str) -> str:
        user_id = validate_id(user_id, kind="user_id")
        with self._connect() as db:
            if not db.execute(
                "SELECT 1 FROM users WHERE user_id=? AND deleted_at IS NULL AND disabled_at IS NULL",
                (user_id,),
            ).fetchone():
                raise UserStoreError("用户不存在", code="account_not_found")
            token, _ = self._new_session(db, user_id, **device)
        return token

    def admin_issue_token(self, user_id: str, **device: str) -> dict[str, str]:
        user_id = validate_id(user_id, kind="user_id")
        with self._connect() as db:
            if not db.execute(
                "SELECT 1 FROM users WHERE user_id=? AND deleted_at IS NULL AND disabled_at IS NULL",
                (user_id,),
            ).fetchone():
                raise UserStoreError("账号不存在或已禁用", code="account_unavailable")
            token, session_id = self._new_session(db, user_id, **device)
        return {"token": token, "session_id": session_id}

    def login(self, email: str, password: str, *, device: dict[str, str] | None = None) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE email=? AND deleted_at IS NULL", (_email(email),)).fetchone()
            if row is None:
                raise UserStoreError("账号不存在", code="account_not_found")
            if row["disabled_at"]:
                raise UserStoreError("账号已被禁用", code="account_disabled")
            if not _password_matches(password, row["password_hash"]):
                raise UserStoreError("密码错误", code="invalid_password")
            if not row["email_verified_at"]:
                raise UserStoreError("需要先验证邮箱", code="email_not_verified")
            token, session_id = self._new_session(db, str(row["user_id"]), **(device or {}))
            return {"account": self._account(row), "token": token, "session_id": session_id}

    def authenticate_identity(self, token: str, *, touch: bool = True) -> AuthIdentity | None:
        if not token:
            return None
        with self._connect() as db:
            row = db.execute("""SELECT s.user_id,s.session_id FROM user_sessions s JOIN users u ON u.user_id=s.user_id
                WHERE s.token_hash=? AND s.revoked_at IS NULL AND u.deleted_at IS NULL
                  AND u.disabled_at IS NULL""", (self._token_hash(token),)).fetchone()
            if row and touch:
                db.execute(
                    "UPDATE user_sessions SET last_seen_at=? WHERE session_id=? AND last_seen_at<?",
                    (_iso(), row["session_id"], _iso(_now() - timedelta(seconds=60))),
                )
        return AuthIdentity(str(row["user_id"]), str(row["session_id"])) if row else None

    def authenticate(self, token: str) -> str | None:
        identity = self.authenticate_identity(token)
        return identity.user_id if identity else None

    def get_account(self, user_id: str, *, db: sqlite3.Connection | None = None) -> dict[str, Any]:
        user_id = validate_id(user_id, kind="user_id")
        def read(conn: sqlite3.Connection) -> dict[str, Any]:
            row = conn.execute("SELECT * FROM users WHERE user_id=? AND deleted_at IS NULL", (user_id,)).fetchone()
            if row is None:
                raise UserStoreError("账号不存在", code="account_not_found")
            return self._account(row)
        if db is not None:
            return read(db)
        with self._connect() as conn:
            return read(conn)

    def update_account(self, user_id: str, *, display_name: str) -> dict[str, Any]:
        name = (display_name or "").strip()
        if not 1 <= len(name) <= 80:
            raise UserStoreError("显示名称长度需为 1–80 个字符", code="invalid_display_name")
        with self._connect() as db:
            result = db.execute("UPDATE users SET display_name=?,updated_at=? WHERE user_id=? AND deleted_at IS NULL",
                                (name, _iso(), validate_id(user_id, kind="user_id")))
            if result.rowcount != 1:
                raise UserStoreError("账号不存在", code="account_not_found")
            return self.get_account(user_id, db=db)

    def change_password(self, user_id: str, old_password: str, new_password: str, *,
                        current_session_id: str | None, revoke_other_sessions: bool = True) -> None:
        _password(new_password)
        user_id = validate_id(user_id, kind="user_id")
        with self._connect() as db:
            row = db.execute("SELECT password_hash FROM users WHERE user_id=? AND deleted_at IS NULL", (user_id,)).fetchone()
            if row is None or not _password_matches(old_password, row["password_hash"]):
                raise UserStoreError("旧密码错误", code="invalid_password")
            db.execute("UPDATE users SET password_hash=?,updated_at=? WHERE user_id=?", (_password_hash(new_password), _iso(), user_id))
            if revoke_other_sessions:
                query = "UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL"
                args: tuple[Any, ...] = (_iso(), user_id)
                if current_session_id:
                    query += " AND session_id<>?"
                    args += (current_session_id,)
                db.execute(query, args)

    def list_sessions(self, user_id: str, *, current_session_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("""SELECT session_id,device_id,device_name,device_type,platform,app_version,created_at,last_seen_at
                FROM user_sessions WHERE user_id=? AND revoked_at IS NULL ORDER BY last_seen_at DESC""",
                (validate_id(user_id, kind="user_id"),)).fetchall()
        return [{**dict(row), "current": row["session_id"] == current_session_id} for row in rows]

    def revoke_session(self, user_id: str, session_id: str) -> bool:
        with self._connect() as db:
            result = db.execute("UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND session_id=? AND revoked_at IS NULL",
                (_iso(), validate_id(user_id, kind="user_id"), validate_id(session_id, kind="session_id")))
        return result.rowcount == 1

    def revoke_other_sessions(self, user_id: str, current_session_id: str | None) -> int:
        query = "UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL"
        args: tuple[Any, ...] = (_iso(), validate_id(user_id, kind="user_id"))
        if current_session_id:
            query += " AND session_id<>?"
            args += (current_session_id,)
        with self._connect() as db:
            return int(db.execute(query, args).rowcount)

    def admin_overview(self) -> dict[str, int]:
        with self._connect() as db:
            row = db.execute("""SELECT
                COUNT(*) AS accounts,
                SUM(CASE WHEN disabled_at IS NOT NULL THEN 1 ELSE 0 END) AS disabled,
                SUM(CASE WHEN email_verified_at IS NOT NULL THEN 1 ELSE 0 END) AS verified
                FROM users WHERE deleted_at IS NULL""").fetchone()
            sessions = db.execute("""SELECT
                SUM(CASE WHEN revoked_at IS NULL THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN revoked_at IS NOT NULL THEN 1 ELSE 0 END) AS revoked
                FROM user_sessions""").fetchone()
        return {
            "accounts": int(row["accounts"] or 0),
            "disabled_accounts": int(row["disabled"] or 0),
            "verified_accounts": int(row["verified"] or 0),
            "active_tokens": int(sessions["active"] or 0),
            "revoked_tokens": int(sessions["revoked"] or 0),
        }

    def admin_list_accounts(self, query: str = "") -> list[dict[str, Any]]:
        query = (query or "").strip().lower()[:200]
        sql = """SELECT u.*,
                SUM(CASE WHEN s.session_id IS NOT NULL AND s.revoked_at IS NULL THEN 1 ELSE 0 END) AS active_tokens,
            MAX(s.last_seen_at) AS last_seen_at
            FROM users u LEFT JOIN user_sessions s ON s.user_id=u.user_id
            WHERE u.deleted_at IS NULL"""
        args: tuple[Any, ...] = ()
        if query:
            sql += " AND (LOWER(COALESCE(u.email,'')) LIKE ? OR LOWER(COALESCE(u.display_name,'')) LIKE ? OR LOWER(u.user_id) LIKE ?)"
            term = f"%{query}%"
            args = (term, term, term)
        sql += " GROUP BY u.user_id ORDER BY u.created_at DESC"
        with self._connect() as db:
            rows = db.execute(sql, args).fetchall()
        return [
            {
                **self._account(row),
                "active_tokens": int(row["active_tokens"] or 0),
                "last_seen_at": str(row["last_seen_at"] or ""),
            }
            for row in rows
        ]

    def admin_get_account(self, user_id: str) -> dict[str, Any]:
        account = self.get_account(user_id)
        sessions = self.admin_list_sessions(user_id)
        return {
            **account,
            "active_tokens": sum(1 for item in sessions if item["active"]),
            "tokens": sessions,
        }

    def admin_list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        user_id = validate_id(user_id, kind="user_id")
        with self._connect() as db:
            rows = db.execute("""SELECT session_id,device_id,device_name,device_type,
                platform,app_version,created_at,last_seen_at,revoked_at,token_hint
                FROM user_sessions WHERE user_id=? ORDER BY created_at DESC""", (user_id,)).fetchall()
        return [
            {
                **dict(row),
                "active": row["revoked_at"] is None,
                "token_hint": f"••••{row['token_hint']}" if row["token_hint"] else "旧版 Token",
            }
            for row in rows
        ]

    def admin_update_account(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        email_verified: bool | None = None,
        disabled: bool | None = None,
    ) -> dict[str, Any]:
        user_id = validate_id(user_id, kind="user_id")
        assignments = ["updated_at=?"]
        args: list[Any] = [_iso()]
        if display_name is not None:
            name = display_name.strip()
            if not 1 <= len(name) <= 80:
                raise UserStoreError("显示名称长度需为 1–80 个字符", code="invalid_display_name")
            assignments.append("display_name=?")
            args.append(name)
        if email_verified is not None:
            assignments.append("email_verified_at=?")
            args.append(_iso() if email_verified else None)
        if disabled is not None:
            assignments.append("disabled_at=?")
            args.append(_iso() if disabled else None)
        args.append(user_id)
        with self._connect() as db:
            result = db.execute(
                f"UPDATE users SET {','.join(assignments)} WHERE user_id=? AND deleted_at IS NULL",
                args,
            )
            if result.rowcount != 1:
                raise UserStoreError("账号不存在", code="account_not_found")
            if disabled:
                db.execute(
                    "UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                    (_iso(), user_id),
                )
            return self.get_account(user_id, db=db)

    def admin_set_password(self, user_id: str, new_password: str) -> int:
        _password(new_password)
        user_id = validate_id(user_id, kind="user_id")
        with self._connect() as db:
            result = db.execute(
                "UPDATE users SET password_hash=?,updated_at=? WHERE user_id=? AND deleted_at IS NULL",
                (_password_hash(new_password), _iso(), user_id),
            )
            if result.rowcount != 1:
                raise UserStoreError("账号不存在", code="account_not_found")
            return int(db.execute(
                "UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (_iso(), user_id),
            ).rowcount)

    def admin_delete_account(self, user_id: str) -> None:
        user_id = validate_id(user_id, kind="user_id")
        with self._connect() as db:
            if not db.execute(
                "SELECT 1 FROM users WHERE user_id=? AND deleted_at IS NULL", (user_id,)
            ).fetchone():
                raise UserStoreError("账号不存在", code="account_not_found")
            deleted = _iso()
            db.execute("DELETE FROM account_codes WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM user_tokens WHERE user_id=?", (user_id,))
            db.execute("""UPDATE users SET email=NULL,password_hash=NULL,display_name='',
                email_verified_at=NULL,disabled_at=NULL,updated_at=?,deleted_at=? WHERE user_id=?""",
                (deleted, deleted, user_id))

    def create_code(self, user_id: str, purpose: str, *, ttl_minutes: int = 15) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        user_id = validate_id(user_id, kind="user_id")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            recent = db.execute("SELECT 1 FROM account_codes WHERE user_id=? AND purpose=? AND created_at>?",
                                (user_id, purpose, _iso(_now() - timedelta(seconds=60)))).fetchone()
            if recent:
                raise UserStoreError("验证码发送过于频繁，请稍后再试", code="code_rate_limited")
            db.execute("UPDATE account_codes SET consumed_at=? WHERE user_id=? AND purpose=? AND consumed_at IS NULL", (_iso(), user_id, purpose))
            db.execute("INSERT INTO account_codes(code_id,user_id,purpose,code_hash,expires_at,created_at) VALUES(?,?,?,?,?,?)", (
                f"code_{uuid.uuid4().hex}", user_id, purpose, self._token_hash(code),
                _iso(_now() + timedelta(minutes=max(1, ttl_minutes))), _iso()))
        return code

    def verify_code(self, email: str, code: str, purpose: str) -> str:
        now = _iso()
        user_id = None
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT c.code_id,c.user_id,c.code_hash,c.failed_attempts
                FROM account_codes c JOIN users u ON u.user_id=c.user_id
                WHERE u.email=? AND u.deleted_at IS NULL AND u.disabled_at IS NULL
                  AND c.purpose=? AND c.consumed_at IS NULL AND c.expires_at>?
                ORDER BY c.created_at DESC LIMIT 1""", (_email(email), purpose, now)).fetchone()
            if row and row["failed_attempts"] < 5:
                if hmac.compare_digest(str(row["code_hash"]), self._token_hash(code.strip())):
                    updated = db.execute("UPDATE account_codes SET consumed_at=? WHERE code_id=? AND consumed_at IS NULL",
                                         (now, row["code_id"]))
                    if updated.rowcount == 1:
                        user_id = str(row["user_id"])
                        if purpose == "verify_email":
                            db.execute("UPDATE users SET email_verified_at=?,updated_at=? WHERE user_id=?", (now, now, user_id))
                else:
                    db.execute("""UPDATE account_codes SET failed_attempts=failed_attempts+1,
                        consumed_at=CASE WHEN failed_attempts>=4 THEN ? ELSE consumed_at END WHERE code_id=?""",
                               (now, row["code_id"]))
        # Raise after commit: failed attempts must not roll back.
        if user_id is None:
            raise UserStoreError("验证码无效、尝试次数过多或已过期", code="invalid_code")
        return user_id

    def verify_email_and_login(self, email: str, code: str, *, device: dict[str, str]) -> dict[str, Any]:
        user_id = self.verify_code(email, code, "verify_email")
        with self._connect() as db:
            token, session_id = self._new_session(db, user_id, **device)
            account = self.get_account(user_id, db=db)
        return {"account": account, "token": token, "session_id": session_id}

    def login_with_email_code(self, email: str, code: str, *, device: dict[str, str]) -> dict[str, Any]:
        user_id = self.verify_code(email, code, "login_email")
        with self._connect() as db:
            token, session_id = self._new_session(db, user_id, **device)
            account = self.get_account(user_id, db=db)
        return {"account": account, "token": token, "session_id": session_id}

    def account_for_email(self, email: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE email=? AND deleted_at IS NULL", (_email(email),)).fetchone()
        return self._account(row) if row else None

    def reset_password(self, email: str, code: str, new_password: str) -> None:
        _password(new_password)
        user_id = self.verify_code(email, code, "reset_password")
        with self._connect() as db:
            db.execute("UPDATE users SET password_hash=?,updated_at=? WHERE user_id=?", (_password_hash(new_password), _iso(), user_id))
            db.execute("UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (_iso(), user_id))

    def delete_account(self, user_id: str, password: str) -> None:
        user_id = validate_id(user_id, kind="user_id")
        with self._connect() as db:
            row = db.execute("SELECT password_hash FROM users WHERE user_id=? AND deleted_at IS NULL", (user_id,)).fetchone()
            if row is None:
                raise UserStoreError("账号不存在", code="account_not_found")
            if row["password_hash"] and not _password_matches(password, row["password_hash"]):
                raise UserStoreError("密码错误", code="invalid_password")
            deleted = _iso()
            db.execute("DELETE FROM account_codes WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM user_tokens WHERE user_id=?", (user_id,))
            db.execute("""UPDATE users SET email=NULL,password_hash=NULL,display_name='',email_verified_at=NULL,
                updated_at=?,deleted_at=? WHERE user_id=?""", (deleted, deleted, user_id))
