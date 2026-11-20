import sys
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from scripts.seed import seed_database


def run_smoke_checks():
    print("=======================================================")
    print(" COMMUTE POLICY ASSISTANT - SMOKE CHECKS")
    print("=======================================================")

    # Ensure baseline fixtures are seeded
    seed_database(reset=False)

    client = TestClient(app)

    # 1. Health Checks
    print("[1/6] Testing health endpoints...")
    resp = client.get("/health/live")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert resp.json().get("status") == "alive"

    resp = client.get("/health/ready")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert resp.json().get("database") == "ok"
    assert resp.json().get("vector_store") == "ok"
    print("  -> /health/live and /health/ready OK")

    # 2. Demo Authentication
    print("[2/6] Testing demo authentication...")
    # Valid employee login
    resp = client.post("/api/v1/demo/login", json={"username": "aurora_employee", "password": "demo123"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    emp_token = resp.json()["access_token"]
    assert resp.json()["company_id"] == "aurora"
    assert resp.json()["role"] == "employee"

    # Valid admin login
    resp = client.post("/api/v1/demo/login", json={"username": "aurora_admin", "password": "demo123"})
    assert resp.status_code == 200
    admin_token = resp.json()["access_token"]
    assert resp.json()["role"] == "admin"

    # Invalid login
    resp = client.post("/api/v1/demo/login", json={"username": "aurora_employee", "password": "wrongpassword"})
    assert resp.status_code == 401
    print("  -> Auth endpoints verified")

    # 3. Grounded Question Answering
    print("[3/6] Testing grounded ask endpoint...")
    headers = {"Authorization": f"Bearer {emp_token}"}
    resp = client.post("/api/v1/ask", headers=headers, json={"question": "Can I book a cab this Saturday?"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["status"] == "answered"
    assert len(data["citations"]) >= 1
    assert data["citations"][0]["document_title"] == "Aurora Employee Transport Policy"
    assert "manager" in data["answer"].lower()
    print("  -> Grounded answer and citation OK")

    # 4. Uncovered Question (Desk Referral)
    print("[4/6] Testing unanswerable question...")
    resp = client.post("/api/v1/ask", headers=headers, json={"question": "Can I bring my pet dog in the commute cab?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "not_covered"
    assert "transport desk" in data["answer"].lower()
    assert len(data["citations"]) == 0
    print("  -> Not covered with transport desk referral OK")

    # 5. Cross-Tenant Isolation
    print("[5/6] Testing tenant isolation / cross-tenant trap...")
    # Aurora user asking about BluePeak
    resp = client.post("/api/v1/ask", headers=headers, json={"question": "What does BluePeak say about escort after 10 PM?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "not_covered"
    for cit in data["citations"]:
        assert "bluepeak" not in cit["document_title"].lower()
    print("  -> Cross-tenant protection OK")

    # 6. Admin Role Guards and Introspection
    print("[6/6] Testing admin guards and active policy metadata...")
    # Employee cannot call admin endpoint
    resp = client.get("/api/v1/admin/policies", headers=headers)
    assert resp.status_code == 403, f"Expected 403 for employee, got {resp.status_code}"

    # Admin can call admin endpoint
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    resp = client.get("/api/v1/admin/policies/active", headers=admin_headers)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert resp.json()["company_id"] == "aurora"
    assert resp.json()["version"] >= 1
    print("  -> Admin role guards verified")

    print("\n=======================================================")
    print(" ALL SMOKE CHECKS PASSED SUCCESSFULLY!")
    print("=======================================================\n")


if __name__ == "__main__":
    try:
        run_smoke_checks()
    except Exception as e:
        print(f"\n[SMOKE CHECK FAILED]: {str(e)}")
        sys.exit(1)
