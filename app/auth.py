import jwt
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.schemas import Principal

security_bearer = HTTPBearer(auto_error=False)


class AuthError(HTTPException):
    def __init__(self, detail: str, status_code: int = status.HTTP_401_UNAUTHORIZED):
        super().__init__(
            status_code=status_code,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


def decode_and_validate_jwt(token: str) -> Dict[str, Any]:
    """
    Strictly decode and validate HS256 JWT claims.
    Rejects alg=none, missing claims, invalid audience/issuer, expired tokens.
    Spec §7.1.
    """
    try:
        # Inspect header algorithm first to explicitly reject 'none' or mismatched algorithms
        unverified_headers = jwt.get_unverified_header(token)
        if unverified_headers.get("alg") != "HS256":
            raise AuthError("Invalid JWT algorithm; only HS256 is accepted")

        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=["HS256"],
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE,
            options={
                "require": ["sub", "company_id", "role", "iss", "aud", "iat", "exp"],
                "verify_iss": True,
                "verify_aud": True,
                "verify_exp": True,
                "verify_iat": True,
            },
        )
    except jwt.ExpiredSignatureError:
        raise AuthError("JWT token has expired")
    except jwt.InvalidAlgorithmError:
        raise AuthError("Invalid JWT algorithm")
    except jwt.InvalidIssuerError:
        raise AuthError("Invalid JWT issuer")
    except jwt.InvalidAudienceError:
        raise AuthError("Invalid JWT audience")
    except (jwt.InvalidTokenError, jwt.DecodeError) as e:
        raise AuthError(f"Malformed or invalid token: {str(e)}")
    except Exception as e:
        raise AuthError(f"Authentication failure: {str(e)}")

    role = payload.get("role")
    if role not in ("employee", "admin"):
        raise AuthError(f"Invalid role claim: {role}")

    company_id = payload.get("company_id")
    if not company_id or not isinstance(company_id, str):
        raise AuthError("Missing or invalid company_id claim")

    return payload


async def get_current_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
) -> Principal:
    """FastAPI dependency to extract and validate authenticated Principal from Bearer token."""
    if not credentials or not credentials.credentials:
        raise AuthError("Missing Authorization header or Bearer token")

    payload = decode_and_validate_jwt(credentials.credentials)

    return Principal(
        user_id=payload["sub"],
        company_id=payload["company_id"],
        role=payload["role"],
        display_name=payload.get("display_name", payload["sub"]),
    )


async def require_admin(
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    """Role guard ensuring current principal possesses admin privileges."""
    if principal.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Admin privileges required for this action",
        )
    return principal
