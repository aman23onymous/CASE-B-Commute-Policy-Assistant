import jwt
import pytest
from app.config import settings
from app.demo_issuer import create_access_token


def test_valid_employee_token_access(client, aurora_token):
    resp = client.post(
        "/api/v1/ask",
        headers={"Authorization": f"Bearer {aurora_token}"},
        json={"question": "Can I book a cab this Saturday?"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "answered"
    assert len(data["citations"]) >= 1


def test_missing_auth_header(client):
    resp = client.post(
        "/api/v1/ask",
        json={"question": "Can I book a cab this Saturday?"},
    )
    assert resp.status_code == 401
    assert "Missing Authorization header" in resp.json()["detail"]


def test_malformed_token(client):
    resp = client.post(
        "/api/v1/ask",
        headers={"Authorization": "Bearer not-a-valid-jwt-token"},
    )
    assert resp.status_code == 401


def test_expired_token(client):
    expired = create_access_token(
        user_id="aurora_emp",
        company_id="aurora",
        role="employee",
        display_name="Expired User",
        ttl_seconds=-3600,
    )
    resp = client.post(
        "/api/v1/ask",
        headers={"Authorization": f"Bearer {expired}"},
        json={"question": "Can I book a cab this Saturday?"},
    )
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


def test_alg_none_rejected(client):
    header = {"alg": "none", "typ": "JWT"}
    payload = {
        "sub": "attacker",
        "company_id": "aurora",
        "role": "admin",
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "exp": 9999999999,
        "iat": 1000,
    }
    # Unsigned token
    raw_token = jwt.encode(payload, key="", algorithm="none")
    resp = client.post(
        "/api/v1/ask",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"question": "Can I book a cab this Saturday?"},
    )
    assert resp.status_code == 401


def test_wrong_secret_signature_rejected(client):
    forged = jwt.encode(
        {
            "sub": "attacker",
            "company_id": "aurora",
            "role": "admin",
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "exp": 9999999999,
            "iat": 1000,
        },
        key="wrong-secret-key-that-does-not-match!",
        algorithm="HS256",
    )
    resp = client.post(
        "/api/v1/ask",
        headers={"Authorization": f"Bearer {forged}"},
        json={"question": "Can I book a cab this Saturday?"},
    )
    assert resp.status_code == 401


def test_employee_cannot_access_admin_endpoint(client, aurora_token):
    resp = client.get(
        "/api/v1/admin/policies",
        headers={"Authorization": f"Bearer {aurora_token}"},
    )
    assert resp.status_code == 403
    assert "Admin privileges required" in resp.json()["detail"]


def test_admin_can_access_own_admin_endpoint(client, aurora_admin_token):
    resp = client.get(
        "/api/v1/admin/policies",
        headers={"Authorization": f"Bearer {aurora_admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    for p in data:
        assert p["company_id"] == "aurora"


def test_request_payload_with_company_id_override_rejected(client, aurora_token):
    # Spec §11.2: extra='forbid' rejects client-provided company_id
    resp = client.post(
        "/api/v1/ask",
        headers={"Authorization": f"Bearer {aurora_token}"},
        json={"question": "Can I book a cab?", "company_id": "bluepeak"},
    )
    assert resp.status_code == 422
