import sqlite3
import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def hash_password(password: str, salt: Optional[str] = None) -> str:
    """Generate salted PBKDF2 hash for demo user passwords."""
    if not salt:
        salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000
    )
    return f"{salt}${dk.hex()}"


def verify_password(plain_password: str, stored_hash: str) -> bool:
    """Verify password against stored salt$hash format."""
    try:
        salt, expected_dk = stored_hash.split("$", 1)
        actual_dk = hashlib.pbkdf2_hmac(
            "sha256",
            plain_password.encode("utf-8"),
            salt.encode("utf-8"),
            100_000
        ).hex()
        return actual_dk == expected_dk
    except Exception:
        return False


def get_db_connection(db_path: str) -> sqlite3.Connection:
    """Get SQLite connection configured for concurrent WAL access and FK enforcement."""
    conn = sqlite3.connect(db_path, timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db(db_path: str) -> None:
    """Idempotently initialize SQLite schema according to spec §5.1."""
    conn = get_db_connection(db_path)
    try:
        with conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS companies (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS policy_versions (
                id TEXT PRIMARY KEY,
                company_id TEXT NOT NULL REFERENCES companies(id),
                version INTEGER NOT NULL CHECK (version > 0),
                title TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                collection_name TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK (status IN ('STAGING','READY','ACTIVE','SUPERSEDED','FAILED')),
                created_at TEXT NOT NULL,
                activated_at TEXT,
                UNIQUE(company_id, version)
            );

            CREATE TABLE IF NOT EXISTS active_policies (
                company_id TEXT PRIMARY KEY REFERENCES companies(id),
                policy_version_id TEXT NOT NULL REFERENCES policy_versions(id)
            );

            CREATE TABLE IF NOT EXISTS demo_users (
                id TEXT PRIMARY KEY,
                company_id TEXT NOT NULL REFERENCES companies(id),
                display_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('employee','admin')),
                password_hash TEXT NOT NULL
            );
            """)
    finally:
        conn.close()


def get_active_policy_snapshot(
    conn: sqlite3.Connection, company_id: str
) -> Optional[Dict[str, Any]]:
    """
    Get a single immutable snapshot of (company_id, active_policy_version_id, version, title, collection_name).
    Single source of truth according to spec §5.1 and §5.3.
    """
    query = """
        SELECT
            ap.company_id,
            pv.id AS policy_version_id,
            pv.version,
            pv.title,
            pv.collection_name,
            pv.status,
            pv.activated_at
        FROM active_policies ap
        JOIN policy_versions pv ON ap.policy_version_id = pv.id
        WHERE ap.company_id = ?
    """
    cursor = conn.execute(query, (company_id,))
    row = cursor.fetchone()
    if row:
        return dict(row)
    return None


def get_company_versions(
    conn: sqlite3.Connection, company_id: str
) -> List[Dict[str, Any]]:
    """List policy versions for a specific company only."""
    query = """
        SELECT
            pv.id,
            pv.company_id,
            pv.version,
            pv.title,
            pv.sha256,
            pv.collection_name,
            pv.status,
            pv.created_at,
            pv.activated_at,
            CASE WHEN ap.policy_version_id IS NOT NULL THEN 1 ELSE 0 END AS is_active
        FROM policy_versions pv
        LEFT JOIN active_policies ap
            ON pv.company_id = ap.company_id AND pv.id = ap.policy_version_id
        WHERE pv.company_id = ?
        ORDER BY pv.version DESC
    """
    cursor = conn.execute(query, (company_id,))
    return [dict(r) for r in cursor.fetchall()]


def get_highest_version(conn: sqlite3.Connection, company_id: str) -> int:
    """Get current highest registered version number for company (0 if none)."""
    cursor = conn.execute(
        "SELECT MAX(version) FROM policy_versions WHERE company_id = ?",
        (company_id,),
    )
    res = cursor.fetchone()[0]
    return res if res is not None else 0


def create_staging_version(
    conn: sqlite3.Connection,
    company_id: str,
    version: int,
    title: str,
    sha256: str,
    collection_name: str,
) -> str:
    """Insert a new staging policy version row."""
    version_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            """
            INSERT INTO policy_versions (
                id, company_id, version, title, sha256, collection_name, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'STAGING', ?)
            """,
            (version_id, company_id, version, title, sha256, collection_name, now_iso),
        )
    return version_id


def mark_version_failed(conn: sqlite3.Connection, version_id: str) -> None:
    """Mark a staged version as FAILED."""
    with conn:
        conn.execute(
            "UPDATE policy_versions SET status = 'FAILED' WHERE id = ?",
            (version_id,),
        )


class VersionConflictError(Exception):
    """Raised when concurrent upload CAS validation fails."""
    pass


def activate_policy_version(
    conn: sqlite3.Connection,
    company_id: str,
    expected_active_version_id: Optional[str],
    new_version_id: str,
) -> None:
    """
    Perform atomic activation as one SQLite transaction with compare-and-swap.
    Spec §6 step 6.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    try:
        # Check current active policy
        cursor.execute(
            "SELECT policy_version_id FROM active_policies WHERE company_id = ?",
            (company_id,),
        )
        current_row = cursor.fetchone()
        current_active = current_row[0] if current_row else None

        if current_active != expected_active_version_id:
            raise VersionConflictError(
                f"Concurrent policy update conflict: expected active {expected_active_version_id}, found {current_active}"
            )

        # Mark previous active version as SUPERSEDED if present
        if current_active:
            cursor.execute(
                "UPDATE policy_versions SET status = 'SUPERSEDED' WHERE id = ?",
                (current_active,),
            )

        # Mark new version as ACTIVE and update activated_at
        cursor.execute(
            "UPDATE policy_versions SET status = 'ACTIVE', activated_at = ? WHERE id = ?",
            (now_iso, new_version_id),
        )

        # Update or insert active_policies single pointer
        cursor.execute(
            """
            INSERT INTO active_policies (company_id, policy_version_id)
            VALUES (?, ?)
            ON CONFLICT(company_id) DO UPDATE SET policy_version_id = excluded.policy_version_id
            """,
            (company_id, new_version_id),
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
