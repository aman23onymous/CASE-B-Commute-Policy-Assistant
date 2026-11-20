import pytest


def test_escort_threshold_contradiction(client, aurora_token, bluepeak_token, cedar_token):
    q = "When is a security escort needed for a woman's drop?"

    # Aurora: strictly after 20:00
    r_a = client.post("/api/v1/ask", headers={"Authorization": f"Bearer {aurora_token}"}, json={"question": q})
    assert r_a.status_code == 200
    d_a = r_a.json()
    assert d_a["status"] == "answered"
    assert "20:00" in d_a["answer"]
    assert d_a["citations"][0]["document_title"] == "Aurora Employee Transport Policy"

    # BluePeak: after 22:00
    r_b = client.post("/api/v1/ask", headers={"Authorization": f"Bearer {bluepeak_token}"}, json={"question": q})
    assert r_b.status_code == 200
    d_b = r_b.json()
    assert d_b["status"] == "answered"
    assert "22:00" in d_b["answer"]
    assert d_b["citations"][0]["document_title"] == "BluePeak Corporate Commute Regulations"

    # Cedar: after 21:00
    r_c = client.post("/api/v1/ask", headers={"Authorization": f"Bearer {cedar_token}"}, json={"question": q})
    assert r_c.status_code == 200
    d_c = r_c.json()
    assert d_c["status"] == "answered"
    assert "21:00" in d_c["answer"]
    assert d_c["citations"][0]["document_title"] == "Cedar Logistics Employee Commute Guidelines"


def test_saturday_cab_contradiction(client, aurora_token, bluepeak_token, cedar_token):
    q = "Can I book a cab this Saturday?"

    # Aurora: manager-approved
    r_a = client.post("/api/v1/ask", headers={"Authorization": f"Bearer {aurora_token}"}, json={"question": q})
    assert r_a.status_code == 200
    assert "manager" in r_a.json()["answer"].lower()

    # BluePeak: not offered
    r_b = client.post("/api/v1/ask", headers={"Authorization": f"Bearer {bluepeak_token}"}, json={"question": q})
    assert r_b.status_code == 200
    assert "not offered" in r_b.json()["answer"].lower()

    # Cedar: rostered shifts
    r_c = client.post("/api/v1/ask", headers={"Authorization": f"Bearer {cedar_token}"}, json={"question": q})
    assert r_c.status_code == 200
    assert "rostered" in r_c.json()["answer"].lower()


def test_airport_approval_contradiction(client, aurora_token, bluepeak_token, cedar_token):
    q = "Who approves an airport cab ride?"

    # Aurora: transport manager
    r_a = client.post("/api/v1/ask", headers={"Authorization": f"Bearer {aurora_token}"}, json={"question": q})
    assert "transport manager" in r_a.json()["answer"].lower()

    # BluePeak: HR + finance
    r_b = client.post("/api/v1/ask", headers={"Authorization": f"Bearer {bluepeak_token}"}, json={"question": q})
    assert "hr + finance" in r_b.json()["answer"].lower() or "finance" in r_b.json()["answer"].lower()

    # Cedar: not covered
    r_c = client.post("/api/v1/ask", headers={"Authorization": f"Bearer {cedar_token}"}, json={"question": q})
    assert r_c.json()["status"] == "not_covered"
    assert len(r_c.json()["citations"]) == 0


def test_cross_tenant_trap_no_leakage(client, aurora_token):
    # Aurora user asking explicitly for BluePeak's escort rule
    resp = client.post(
        "/api/v1/ask",
        headers={"Authorization": f"Bearer {aurora_token}"},
        json={"question": "What does BluePeak say about escort after 10 PM?"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "not_covered"
    # Must not contain BluePeak text or citations
    for cit in data["citations"]:
        assert "bluepeak" not in cit["document_title"].lower()
    assert "transport desk" in data["answer"].lower()
