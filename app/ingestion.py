import sqlite3
from typing import Optional
from fastapi import HTTPException, status

from app.chunking import chunk_policy
from app.config import settings
from app.db import (
    activate_policy_version,
    create_staging_version,
    get_active_policy_snapshot,
    get_highest_version,
    mark_version_failed,
    VersionConflictError,
)
from app.embedder import Embedder
from app.policy_parser import PolicyParseError, parse_policy_document
from app.vector_store import (
    ChromaVectorStore,
    sanitize_collection_name,
    VectorStoreError,
)


class PolicyIngestionService:
    def __init__(self, db_conn: sqlite3.Connection, vector_store: ChromaVectorStore, embedder: Embedder):
        self.db_conn = db_conn
        self.vector_store = vector_store
        self.embedder = embedder

    def ingest(
        self,
        company_id: str,
        version: int,
        title: str,
        raw_content: str,
        filename: Optional[str] = None,
    ) -> dict:
        """
        Execute atomic policy ingestion according to Spec §6.
        Never half-activates on failure. Leaves existing active pointer untouched on error.
        """
        # 1. Validation checks on file size, extension, encoding
        if filename:
            lower_name = filename.lower()
            if not (lower_name.endswith(".md") or lower_name.endswith(".txt")):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Invalid file format. Only .md and .txt files are supported.",
                )

        byte_len = len(raw_content.encode("utf-8"))
        if byte_len > settings.MAX_POLICY_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Policy exceeds maximum size limit of {settings.MAX_POLICY_BYTES} bytes.",
            )

        if not raw_content.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Policy document content cannot be empty.",
            )

        # Monotonic version check
        current_highest = get_highest_version(self.db_conn, company_id)
        if version <= current_highest:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Version {version} must be strictly greater than current highest version {current_highest}.",
            )

        # Snapshot current active version before staging (for compare-and-swap)
        active_snapshot = get_active_policy_snapshot(self.db_conn, company_id)
        expected_active_version_id = (
            active_snapshot["policy_version_id"] if active_snapshot else None
        )

        # 2. Parse sections and validate structure before vector store touch
        try:
            parsed = parse_policy_document(raw_content, fallback_title=title)
        except PolicyParseError as pe:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Document parsing failed: {str(pe)}",
            )

        # Generate unique staging collection and record
        collection_name = sanitize_collection_name(company_id, version)
        staging_version_id = create_staging_version(
            conn=self.db_conn,
            company_id=company_id,
            version=version,
            title=parsed.document_title or title,
            sha256=parsed.sha256,
            collection_name=collection_name,
        )

        # 3. Deterministically chunk policy
        chunks = chunk_policy(
            parsed=parsed,
            company_id=company_id,
            policy_version_id=staging_version_id,
            version=version,
        )

        # 4. Populate Chroma staging collection & verify
        try:
            inserted_count = self.vector_store.populate_staging_collection(
                collection_name=collection_name,
                chunks=chunks,
                embedder=self.embedder,
            )

            # Verification step: run sample search in staging collection
            sample_search = self.vector_store.search_active_collection(
                collection_name=collection_name,
                query_text="cab commute transport policy",
                expected_company_id=company_id,
                expected_version_id=staging_version_id,
                embedder=self.embedder,
                n_results=1,
            )
            if not sample_search and inserted_count > 0:
                raise VectorStoreError("Staging sample verification query returned 0 results.")

        except Exception as e:
            # On vector store failure: mark FAILED, clean up collection, leave active pointer unchanged
            mark_version_failed(self.db_conn, staging_version_id)
            self.vector_store.delete_collection_safely(collection_name)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Vector store indexing failure: {str(e)}",
            )

        # 5. Atomic activation in SQLite with compare-and-swap
        try:
            activate_policy_version(
                conn=self.db_conn,
                company_id=company_id,
                expected_active_version_id=expected_active_version_id,
                new_version_id=staging_version_id,
            )
        except VersionConflictError as ce:
            mark_version_failed(self.db_conn, staging_version_id)
            self.vector_store.delete_collection_safely(collection_name)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(ce),
            )
        except Exception as e:
            mark_version_failed(self.db_conn, staging_version_id)
            self.vector_store.delete_collection_safely(collection_name)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database activation transaction failure: {str(e)}",
            )

        return {
            "policy_id": staging_version_id,
            "company_id": company_id,
            "version": version,
            "title": parsed.document_title or title,
            "status": "ACTIVE",
            "chunk_count": inserted_count,
            "collection_name": collection_name,
        }
