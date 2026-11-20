import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt

from app.config import settings
from app.db import verify_password
from app.schemas import Principal, TokenResponse


def create_access_token(
    user_id: str,
    company_id: str,
    role: str,
    display_name: str,
    ttl_seconds: Optional[int] = None,
) -> str:
    """
    Issue a signed HS256 JWT token with required claims.
    Spec §7.1.
    """
    now = datetime.now(timezone.utc)
    ttl = ttl_seconds if ttl_seconds is not None else settings.JWT_TTL_SECONDS
    exp = now + timedelta(seconds=ttl)

    payload = {
        "sub": user_id,
        "company_id": company_id,
        "role": role,
        "display_name": display_name,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }

    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def authenticate_demo_user(
    conn: sqlite3.Connection, username: str, password: str
) -> Optional[Principal]:
    """Verify demo username and password against SQLite demo_users."""
    cursor = conn.execute(
        "SELECT id, company_id, display_name, role, password_hash FROM demo_users WHERE id = ?",
        (username,),
    )
    row = cursor.fetchone()
    if not row:
        return None

    if not verify_password(password, row["password_hash"]):
        return None

    return Principal(
        user_id=row["id"],
        company_id=row["company_id"],
        role=row["role"],
        display_name=row["display_name"],
    )


def issue_token_for_user(principal: Principal) -> TokenResponse:
    """Helper to mint a TokenResponse for a verified principal."""
    token = create_access_token(
        user_id=principal.user_id,
        company_id=principal.company_id,
        role=principal.role,
        display_name=principal.display_name,
    )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.JWT_TTL_SECONDS,
        company_id=principal.company_id,
        role=principal.role,
        display_name=principal.display_name,
    )
