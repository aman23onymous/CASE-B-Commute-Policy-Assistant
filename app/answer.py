import asyncio
import json
import sqlite3
import uuid
from typing import Optional
from fastapi import HTTPException, status

from app.config import settings
from app.db import get_active_policy_snapshot
from app.llm import LLMError, LLMProvider, LLMCallSpy
from app.metrics import Timer, log_structured_event
from app.prompts import SYSTEM_PROMPT, build_answer_prompt
from app.retrieval import Retriever
from app.schemas import AskResponse, Citation, Principal, TimingMs

NOT_COVERED_TEXT = "This is not covered by your company's current transport policy. Please contact your transport desk."
RETRY_ERROR_TEXT = "We couldn't check the policy right now. Please try again shortly."


class AnswerService:
    def __init__(
        self,
        db_conn: sqlite3.Connection,
        retriever: Retriever,
        llm_provider: LLMProvider,
    ):
        self.db_conn = db_conn
        self.retriever = retriever
        self.llm_provider = llm_provider

    async def ask(
        self,
        principal: Principal,
        question: str,
        retrieval_mode: Optional[str] = None,
    ) -> AskResponse:
        """
        Orchestrate answer generation under strict call and timeout budget.
        Spec §10 and §11.2.
        """
        request_id = str(uuid.uuid4())
        total_timer = Timer()
        timing = {"retrieval": 0.0, "llm": 0.0, "total": 0.0}

        # 1. Snapshot active policy for authenticated tenant
        active_snapshot = get_active_policy_snapshot(self.db_conn, principal.company_id)
        if not active_snapshot:
            # No active policy registered for this company
            timing["total"] = total_timer.stop()
            return AskResponse(
                request_id=request_id,
                status="not_covered",
                answer=NOT_COVERED_TEXT,
                citations=[],
                timing_ms=TimingMs(**timing),
            )

        collection_name = active_snapshot["collection_name"]
        version_id = active_snapshot["policy_version_id"]

        # 2. Retrieve scoped candidate chunks with bounded timeout
        retrieval_timer = Timer()
        try:
            # Run in threadpool to avoid blocking event loop
            evidence = await asyncio.wait_for(
                asyncio.to_thread(
                    self.retriever.retrieve,
                    query=question,
                    company_id=principal.company_id,
                    policy_version_id=version_id,
                    collection_name=collection_name,
                    mode=retrieval_mode,
                    max_citations=3,
                ),
                timeout=settings.RETRIEVAL_TIMEOUT_SECONDS,
            )
            timing["retrieval"] = round(retrieval_timer.stop(), 2)
        except asyncio.TimeoutError:
            timing["retrieval"] = round(retrieval_timer.stop(), 2)
            log_structured_event(
                event_type="retrieval_timeout",
                request_id=request_id,
                company_id=principal.company_id,
                version_id=version_id,
                status="timeout",
                timing_ms=timing,
            )
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=RETRY_ERROR_TEXT,
            )
        except Exception as e:
            timing["retrieval"] = round(retrieval_timer.stop(), 2)
            log_structured_event(
                event_type="retrieval_error",
                request_id=request_id,
                company_id=principal.company_id,
                version_id=version_id,
                status="error",
                extra={"error": str(e)},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=RETRY_ERROR_TEXT,
            )

        # 3. Gating check: if no evidence found, return not_covered (0 LLM calls)
        if not evidence:
            timing["total"] = round(total_timer.stop(), 2)
            log_structured_event(
                event_type="ask_abstain_no_evidence",
                request_id=request_id,
                company_id=principal.company_id,
                version_id=version_id,
                retrieval_mode=retrieval_mode or settings.RETRIEVAL_MODE,
                num_candidates=0,
                citation_ids=[],
                llm_calls=0,
                status="not_covered",
                timing_ms=timing,
            )
            return AskResponse(
                request_id=request_id,
                status="not_covered",
                answer=NOT_COVERED_TEXT,
                citations=[],
                timing_ms=TimingMs(**timing),
            )

        # 4. Construct tightly bounded prompt
        user_prompt, chunk_map = build_answer_prompt(question, evidence)

        # 5. Exactly 1 LLM call with strict timeout
        llm_spy = LLMCallSpy(self.llm_provider)
        llm_timer = Timer()
        try:
            raw_response = await asyncio.wait_for(
                asyncio.to_thread(
                    llm_spy.generate,
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    timeout=settings.LLM_TIMEOUT_SECONDS,
                ),
                timeout=settings.LLM_TIMEOUT_SECONDS,
            )
            timing["llm"] = round(llm_timer.stop(), 2)
        except (asyncio.TimeoutError, LLMError) as e:
            timing["llm"] = round(llm_timer.stop(), 2)
            log_structured_event(
                event_type="llm_timeout_or_error",
                request_id=request_id,
                company_id=principal.company_id,
                version_id=version_id,
                status="error",
                extra={"error": str(e)},
            )
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT
                if isinstance(e, asyncio.TimeoutError)
                else status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=RETRY_ERROR_TEXT,
            )
        except Exception as e:
            timing["llm"] = round(llm_timer.stop(), 2)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=RETRY_ERROR_TEXT,
            )

        # 6. Parse and validate LLM output
        try:
            # Clean markdown fences if model inadvertently wraps JSON
            clean_json = raw_response.strip()
            if clean_json.startswith("```"):
                clean_json = clean_json.strip("`")
                if clean_json.startswith("json"):
                    clean_json = clean_json[4:].strip()
            parsed_data = json.loads(clean_json)
        except Exception:
            # Fail closed on non-JSON response (§10.2)
            timing["total"] = round(total_timer.stop(), 2)
            return AskResponse(
                request_id=request_id,
                status="not_covered",
                answer=NOT_COVERED_TEXT,
                citations=[],
                timing_ms=TimingMs(**timing),
            )

        ans_status = parsed_data.get("status")
        if ans_status not in ("answered", "not_covered"):
            # Unknown status -> fail closed
            ans_status = "not_covered"

        if ans_status == "not_covered":
            timing["total"] = round(total_timer.stop(), 2)
            log_structured_event(
                event_type="ask_completed",
                request_id=request_id,
                company_id=principal.company_id,
                version_id=version_id,
                retrieval_mode=retrieval_mode or settings.RETRIEVAL_MODE,
                num_candidates=len(evidence),
                citation_ids=[],
                llm_calls=llm_spy.call_count,
                status="not_covered",
                timing_ms=timing,
            )
            return AskResponse(
                request_id=request_id,
                status="not_covered",
                answer=NOT_COVERED_TEXT,
                citations=[],
                timing_ms=TimingMs(**timing),
            )

        # For answered, strictly validate citation IDs against provided chunks
        raw_cids = parsed_data.get("citation_ids", [])
        if not isinstance(raw_cids, list) or not raw_cids:
            # Answered status with missing citations -> fail closed to not_covered
            timing["total"] = round(total_timer.stop(), 2)
            return AskResponse(
                request_id=request_id,
                status="not_covered",
                answer=NOT_COVERED_TEXT,
                citations=[],
                timing_ms=TimingMs(**timing),
            )

        verified_citations = []
        for cid in raw_cids[:3]:
            if cid in chunk_map:
                cand = chunk_map[cid]
                meta = cand.get("metadata", {})
                verified_citations.append(
                    Citation(
                        document_title=meta.get("document_title", active_snapshot["title"]),
                        version=int(meta.get("version", active_snapshot["version"])),
                        section_id=str(meta.get("section_id", "")),
                        section_title=str(meta.get("section_title", "")),
                        chunk_id=cand.get("chunk_id", ""),
                        excerpt=meta.get("excerpt", cand.get("text", "")[:200]),
                    )
                )

        if not verified_citations:
            # Citations did not match any active evidence chunks
            timing["total"] = round(total_timer.stop(), 2)
            return AskResponse(
                request_id=request_id,
                status="not_covered",
                answer=NOT_COVERED_TEXT,
                citations=[],
                timing_ms=TimingMs(**timing),
            )

        timing["total"] = round(total_timer.stop(), 2)
        answer_text = parsed_data.get("answer", "").strip()
        if not answer_text:
            answer_text = NOT_COVERED_TEXT
            ans_status = "not_covered"
            verified_citations = []

        log_structured_event(
            event_type="ask_completed",
            request_id=request_id,
            company_id=principal.company_id,
            version_id=version_id,
            retrieval_mode=retrieval_mode or settings.RETRIEVAL_MODE,
            num_candidates=len(evidence),
            citation_ids=[c.chunk_id for c in verified_citations],
            llm_calls=llm_spy.call_count,
            status=ans_status,
            timing_ms=timing,
        )

        return AskResponse(
            request_id=request_id,
            status=ans_status,
            answer=answer_text,
            citations=verified_citations,
            timing_ms=TimingMs(**timing),
        )
