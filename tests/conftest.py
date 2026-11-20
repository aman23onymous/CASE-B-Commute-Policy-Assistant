import uuid
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import (
    get_db_connection,
    hash_password,
    init_db,
)
from app.demo_issuer import create_access_token
from app.embedder import get_embedder
from app.ingestion import PolicyIngestionService
from app.main import app
from app.vector_store import ChromaVectorStore


@pytest.fixture(scope="session")
def base_test_dir(tmp_path_factory):
    base_dir = tmp_path_factory.mktemp("commute_test_env")
    yield base_dir


@pytest.fixture(autouse=True)
def reset_fixtures_per_test(base_test_dir):
    uid = uuid.uuid4().hex[:8]
    sqlite_file = base_test_dir / f"test_reg_{uid}.sqlite3"
    chroma_dir = base_test_dir / f"test_chroma_{uid}"

    settings.SQLITE_PATH = str(sqlite_file)
    settings.CHROMA_PATH = str(chroma_dir)
    settings.LLM_PROVIDER = "fake"
    settings.DEMO_MODE = True

    init_db(str(sqlite_file))
    conn = get_db_connection(str(sqlite_file))

    with conn:
        conn.executemany(
            "INSERT INTO companies (id, display_name) VALUES (?, ?)",
            [
                ("aurora", "Aurora Healthcare Systems"),
                ("bluepeak", "BluePeak Technologies"),
                ("cedar", "Cedar Logistics"),
            ],
        )
        pwd = hash_password("demo123", salt="fixed-test-salt")
        conn.executemany(
            "INSERT INTO demo_users (id, company_id, display_name, role, password_hash) VALUES (?, ?, ?, ?, ?)",
            [
                ("aurora_emp", "aurora", "Aurora Emp", "employee", pwd),
                ("aurora_adm", "aurora", "Aurora Admin", "admin", pwd),
                ("bluepeak_emp", "bluepeak", "BluePeak Emp", "employee", pwd),
                ("bluepeak_adm", "bluepeak", "BluePeak Admin", "admin", pwd),
                ("cedar_emp", "cedar", "Cedar Emp", "employee", pwd),
                ("cedar_adm", "cedar", "Cedar Admin", "admin", pwd),
            ],
        )

    root = Path(__file__).resolve().parent.parent
    vector_store = ChromaVectorStore(str(chroma_dir))
    embedder = get_embedder()
    service = PolicyIngestionService(conn, vector_store, embedder)

    fixtures = [
        ("aurora", 1, "Aurora Employee Transport Policy", "policies/aurora_v1.md"),
        ("bluepeak", 1, "BluePeak Corporate Commute Regulations", "policies/bluepeak_v1.md"),
        ("cedar", 1, "Cedar Logistics Employee Commute Guidelines", "policies/cedar_v1.md"),
    ]
    for cid, ver, title, fpath in fixtures:
        raw_text = (root / fpath).read_text(encoding="utf-8")
        service.ingest(cid, ver, title, raw_text, fpath)

    conn.close()

    yield
    # No rmtree during session so Chroma memory-mapped segments stay valid throughout test run


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def aurora_token():
    return create_access_token(
        user_id="aurora_emp",
        company_id="aurora",
        role="employee",
        display_name="Aurora Emp",
    )


@pytest.fixture
def aurora_admin_token():
    return create_access_token(
        user_id="aurora_adm",
        company_id="aurora",
        role="admin",
        display_name="Aurora Admin",
    )


@pytest.fixture
def bluepeak_token():
    return create_access_token(
        user_id="bluepeak_emp",
        company_id="bluepeak",
        role="employee",
        display_name="BluePeak Emp",
    )


@pytest.fixture
def bluepeak_admin_token():
    return create_access_token(
        user_id="bluepeak_adm",
        company_id="bluepeak",
        role="admin",
        display_name="BluePeak Admin",
    )


@pytest.fixture
def cedar_token():
    return create_access_token(
        user_id="cedar_emp",
        company_id="cedar",
        role="employee",
        display_name="Cedar Emp",
    )
