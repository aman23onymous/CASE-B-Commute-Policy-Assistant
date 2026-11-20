from typing import List
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.auth import require_admin
from app.config import settings
from app.db import (
    get_active_policy_snapshot,
    get_company_versions,
    get_db_connection,
)
from app.embedder import get_embedder
from app.ingestion import PolicyIngestionService
from app.schemas import (
    ActivePolicyResponse,
    PolicyUploadResponse,
    PolicyVersionSummary,
    Principal,
)
from app.vector_store import ChromaVectorStore

router = APIRouter(prefix="/api/v1/admin/policies", tags=["Policy Administration"])


def get_db():
    conn = get_db_connection(settings.SQLITE_PATH)
    try:
        yield conn
    finally:
        conn.close()


@router.post(
    "",
    response_model=PolicyUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and atomically activate a new policy version",
)
async def upload_policy(
    version: int = Form(...),
    title: str = Form(...),
    file: UploadFile = File(...),
    admin: Principal = Depends(require_admin),
    db=Depends(get_db),
):
    """
    Upload a replacement policy document for the admin's company.
    Enforces atomic activation and validates structure before activation.
    Spec §6 and §11.3.
    """
    content_bytes = await file.read()
    try:
        raw_text = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file must be valid UTF-8 encoded text.",
        )

    vector_store = ChromaVectorStore(settings.CHROMA_PATH)
    embedder = get_embedder()
    service = PolicyIngestionService(db, vector_store, embedder)

    res = service.ingest(
        company_id=admin.company_id,
        version=version,
        title=title,
        raw_content=raw_text,
        filename=file.filename,
    )

    return PolicyUploadResponse(
        message="Policy ingested and activated successfully.",
        policy_id=res["policy_id"],
        version=res["version"],
        status=res["status"],
        chunk_count=res["chunk_count"],
    )


@router.get(
    "",
    response_model=List[PolicyVersionSummary],
    summary="List all policy versions for authenticated admin's company",
)
def list_company_policies(
    admin: Principal = Depends(require_admin),
    db=Depends(get_db),
):
    """List historical and active versions strictly for the authenticated admin's company."""
    rows = get_company_versions(db, admin.company_id)
    return [
        PolicyVersionSummary(
            id=r["id"],
            company_id=r["company_id"],
            version=r["version"],
            title=r["title"],
            sha256=r["sha256"],
            collection_name=r["collection_name"],
            status=r["status"],
            created_at=r["created_at"],
            activated_at=r.get("activated_at"),
            is_active=bool(r.get("is_active")),
        )
        for r in rows
    ]


@router.get(
    "/active",
    response_model=ActivePolicyResponse,
    summary="Get current active policy for authenticated company",
)
def get_current_active_policy(
    admin: Principal = Depends(require_admin),
    db=Depends(get_db),
):
    """Get authoritative active policy pointer snapshot."""
    active = get_active_policy_snapshot(db, admin.company_id)
    if not active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active policy found for company '{admin.company_id}'.",
        )
    return ActivePolicyResponse(
        company_id=active["company_id"],
        policy_version_id=active["policy_version_id"],
        version=active["version"],
        title=active["title"],
        collection_name=active["collection_name"],
        activated_at=active.get("activated_at"),
    )
