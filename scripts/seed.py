import argparse
import os
import shutil
from pathlib import Path

from app.config import settings
from app.db import (
    get_active_policy_snapshot,
    get_db_connection,
    hash_password,
    init_db,
)
from app.embedder import get_embedder
from app.ingestion import PolicyIngestionService
from app.vector_store import ChromaVectorStore

COMPANIES = [
    {"id": "aurora", "display_name": "Aurora Healthcare Systems"},
    {"id": "bluepeak", "display_name": "BluePeak Technologies"},
    {"id": "cedar", "display_name": "Cedar Logistics"},
]

DEMO_USERS = [
    {"id": "aurora_employee", "company_id": "aurora", "role": "employee", "display_name": "Alice (Aurora Staff)"},
    {"id": "aurora_admin", "company_id": "aurora", "role": "admin", "display_name": "Arthur (Aurora Transport Admin)"},
    {"id": "bluepeak_employee", "company_id": "bluepeak", "role": "employee", "display_name": "Bob (BluePeak Staff)"},
    {"id": "bluepeak_admin", "company_id": "bluepeak", "role": "admin", "display_name": "Brenda (BluePeak Facilities Admin)"},
    {"id": "cedar_employee", "company_id": "cedar", "role": "employee", "display_name": "Charlie (Cedar Staff)"},
    {"id": "cedar_admin", "company_id": "cedar", "role": "admin", "display_name": "Clara (Cedar Logistics Admin)"},
]

POLICY_FIXTURES = [
    {"company_id": "aurora", "version": 1, "title": "Aurora Employee Transport Policy", "file": "policies/aurora_v1.md"},
    {"company_id": "bluepeak", "version": 1, "title": "BluePeak Corporate Commute Regulations", "file": "policies/bluepeak_v1.md"},
    {"company_id": "cedar", "version": 1, "title": "Cedar Logistics Employee Commute Guidelines", "file": "policies/cedar_v1.md"},
]


def seed_database(reset: bool = False, use_fake_embedder: bool = False) -> None:
    """Idempotently seed database and Chroma collections with initial active policies."""
    settings.ensure_directories()

    if reset:
        print("[SEED] Resetting database and vector stores...")
        if os.path.exists(settings.SQLITE_PATH):
            os.remove(settings.SQLITE_PATH)
        if os.path.exists(settings.CHROMA_PATH):
            shutil.rmtree(settings.CHROMA_PATH)
        settings.ensure_directories()

    init_db(settings.SQLITE_PATH)
    conn = get_db_connection(settings.SQLITE_PATH)

    with conn:
        # Seed companies
        for c in COMPANIES:
            conn.execute(
                "INSERT INTO companies (id, display_name) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name",
                (c["id"], c["display_name"]),
            )

        # Seed demo users (default demo password: demo123)
        pwd_hash = hash_password("demo123", salt="fixed-demo-salt-1234")
        for u in DEMO_USERS:
            conn.execute(
                """
                INSERT INTO demo_users (id, company_id, display_name, role, password_hash)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    company_id = excluded.company_id,
                    display_name = excluded.display_name,
                    role = excluded.role,
                    password_hash = excluded.password_hash
                """,
                (u["id"], u["company_id"], u["display_name"], u["role"], pwd_hash),
            )

    vector_store = ChromaVectorStore(settings.CHROMA_PATH)
    embedder = get_embedder(force_fake=use_fake_embedder)
    service = PolicyIngestionService(conn, vector_store, embedder)

    root_dir = Path(__file__).resolve().parent.parent

    # Seed v1 policies if not already active
    for pf in POLICY_FIXTURES:
        active = get_active_policy_snapshot(conn, pf["company_id"])
        if active and active["version"] >= pf["version"]:
            print(f"[SEED] Company '{pf['company_id']}' already has active policy v{active['version']}. Skipping.")
            continue

        policy_path = root_dir / pf["file"]
        if not policy_path.exists():
            print(f"[SEED] WARNING: Policy fixture {policy_path} not found.")
            continue

        raw_text = policy_path.read_text(encoding="utf-8")
        print(f"[SEED] Ingesting {pf['company_id']} v{pf['version']} from {pf['file']}...")
        res = service.ingest(
            company_id=pf["company_id"],
            version=pf["version"],
            title=pf["title"],
            raw_content=raw_text,
            filename=pf["file"],
        )
        print(f"[SEED] -> Activated {res['title']} (v{res['version']}) with {res['chunk_count']} chunks.")

    conn.close()
    print("[SEED] Seeding successfully completed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed demo companies, users and policies")
    parser.add_argument("--reset", action="store_true", help="Clear SQLite and Chroma data before seeding")
    parser.add_argument("--fake-embedder", action="store_true", help="Use deterministic offline embedder")
    args = parser.parse_args()

    seed_database(reset=args.reset, use_fake_embedder=args.fake_embedder)
