def test_health_endpoints(client):
    r_live = client.get("/health/live")
    assert r_live.status_code == 200
    assert r_live.json() == {"status": "alive"}

    r_ready = client.get("/health/ready")
    assert r_ready.status_code == 200
    data = r_ready.json()
    assert data["status"] == "ready"
    assert data["database"] == "ok"
    assert data["vector_store"] == "ok"


def test_ui_serves_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Commute Policy Assistant" in resp.text


def test_ask_response_headers(client, aurora_token):
    resp = client.post(
        "/api/v1/ask",
        headers={"Authorization": f"Bearer {aurora_token}"},
        json={"question": "Can I book a cab this Saturday?"},
    )
    assert resp.status_code == 200
    assert "X-Request-ID" in resp.headers
    assert "Server-Timing" in resp.headers
    assert len(resp.headers["X-Request-ID"]) > 10


def test_admin_policy_history_scoped(client, bluepeak_admin_token):
    resp = client.get(
        "/api/v1/admin/policies",
        headers={"Authorization": f"Bearer {bluepeak_admin_token}"},
    )
    assert resp.status_code == 200
    versions = resp.json()
    assert len(versions) >= 1
    for v in versions:
        assert v["company_id"] == "bluepeak"
        assert "Aurora" not in v["title"]
