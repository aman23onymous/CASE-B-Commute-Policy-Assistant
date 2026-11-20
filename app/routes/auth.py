from fastapi import APIRouter, Depends, HTTPException, status
from app.config import settings
from app.db import get_db_connection
from app.demo_issuer import authenticate_demo_user, issue_token_for_user
from app.schemas import TokenRequest, TokenResponse

router = APIRouter(prefix="/api/v1/demo", tags=["Authentication (Demo Only)"])


def get_db():
    conn = get_db_connection(settings.SQLITE_PATH)
    try:
        yield conn
    finally:
        conn.close()


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Issue demo JWT for seeded fixtures",
)
def demo_login(payload: TokenRequest, db=Depends(get_db)):
    """
    Authenticate predefined demo accounts against seeded SQLite credentials.
    Spec §7.1 and §11.1.
    """
    principal = authenticate_demo_user(db, payload.username, payload.password)
    if not principal:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return issue_token_for_user(principal)
