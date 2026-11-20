import io
from pathlib import Path
import pytest
from app.db import (
    activate_policy_version,
    get_active_policy_snapshot,
    get_db_connection,
    VersionConflictError,
)
from app.config import settings


def test_atomic_v2_activation_and_rule_update(client, aurora_admin_token, aurora_token):
    # Verify current v1 rule for Aurora: Saturday cab is manager-approved
    r_v1 = client.post(
        "/api/v1/ask",
        headers={"Authorization": f"Bearer {aurora_token}"},
        json={"question": "Can I book a cab this Saturday?"},
    )
    assert r_v1.status_code == 200
    assert "manager" in r_v1.json()["answer"].lower()
    assert r_v1.json()["citations"][0]["version"] == 1

    # Upload Aurora v2 (where Saturday is not offered and escort is 21:00)
    root = Path(__file__).resolve().parent.parent
    v2_content = (root / "policies/aurora_v2.md").read_bytes()

    upload_resp = client.post(
        "/api/v1/admin/policies",
        headers={"Authorization": f"Bearer {aurora_admin_token}"},
        data={"version": 2, "title": "Aurora Employee Transport Policy"},
        files={"file": ("aurora_v2.md", io.BytesIO(v2_content), "text/markdown")},
    )
    assert upload_resp.status_code == 201
    data = upload_resp.json()
    assert data["version"] == 2
    assert data["status"] == "ACTIVE"

    # Immediately re-ask: only v2 applies!
    r_v2 = client.post(
        "/api/v1/ask",
        headers={"Authorization": f"Bearer {aurora_token}"},
        json={"question": "Can I book a cab this Saturday?"},
    )
    assert r_v2.status_code == 200
    assert "not offered" in r_v2.json()["answer"].lower()
    assert r_v2.json()["citations"][0]["version"] == 2
    # Verify v1 is no longer cited
    for cit in r_v2.json()["citations"]:
        assert cit["version"] == 2

    # Verify escort changed from 20:00 to 21:00 in v2
    r_escort = client.post(
        "/api/v1/ask",
        headers={"Authorization": f"Bearer {aurora_token}"},
        json={"question": "When is a security escort needed for a woman's drop?"},
    )
    assert r_escort.status_code == 200
    assert "21:00" in r_escort.json()["answer"]
    assert r_escort.json()["citations"][0]["version"] == 2


def test_invalid_file_extension_rejected(client, aurora_admin_token):
    resp = client.post(
        "/api/v1/admin/policies",
        headers={"Authorization": f"Bearer {aurora_admin_token}"},
        data={"version": 3, "title": "Invalid Extension"},
        files={"file": ("policy.pdf", io.BytesIO(b"%PDF-1.4 binary data"), "application/pdf")},
    )
    assert resp.status_code == 422
    assert "Only .md and .txt files are supported" in resp.json()["detail"]


def test_empty_content_rejected(client, aurora_admin_token):
    resp = client.post(
        "/api/v1/admin/policies",
        headers={"Authorization": f"Bearer {aurora_admin_token}"},
        data={"version": 3, "title": "Empty Policy"},
        files={"file": ("empty.md", io.BytesIO(b"   \n\n  "), "text/markdown")},
    )
    assert resp.status_code == 422


def test_unstructured_document_rejected(client, aurora_admin_token):
    unstructured = b"# Title\nThis is just a paragraph without any structured ## section headings."
    resp = client.post(
        "/api/v1/admin/policies",
        headers={"Authorization": f"Bearer {aurora_admin_token}"},
        data={"version": 3, "title": "Unstructured Policy"},
        files={"file": ("unstructured.md", io.BytesIO(unstructured), "text/markdown")},
    )
    assert resp.status_code == 422
    assert "Document lacks valid structured sections" in resp.json()["detail"]


def test_non_monotonic_version_rejected(client, aurora_admin_token):
    # Uploading version 1 when version 2 is already active
    root = Path(__file__).resolve().parent.parent
    v1_content = (root / "policies/aurora_v1.md").read_bytes()

    resp = client.post(
        "/api/v1/admin/policies",
        headers={"Authorization": f"Bearer {aurora_admin_token}"},
        data={"version": 1, "title": "Aurora v1"},
        files={"file": ("aurora_v1.md", io.BytesIO(v1_content), "text/markdown")},
    )
    assert resp.status_code == 409
    assert "must be strictly greater than" in resp.json()["detail"]


def test_cas_conflict_detection():
    conn = get_db_connection(settings.SQLITE_PATH)
    try:
        # Intentionally mismatch expected active version
        with pytest.raises(VersionConflictError):
            activate_policy_version(
                conn=conn,
                company_id="bluepeak",
                expected_active_version_id="intentionally-wrong-expected-id",
                new_version_id="new-staged-id",
            )
    finally:
        conn.close()
