import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("commute_assistant")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        '{"time": "%(asctime)s", "level": "%(levelname)s", "event": %(message)s}'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def log_structured_event(
    event_type: str,
    request_id: str,
    company_id: Optional[str] = None,
    version_id: Optional[str] = None,
    retrieval_mode: Optional[str] = None,
    num_candidates: Optional[int] = None,
    citation_ids: Optional[List[str]] = None,
    llm_calls: int = 0,
    status: Optional[str] = None,
    timing_ms: Optional[Dict[str, float]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Safe structured logging without leaking sensitive questions or raw policy text."""
    payload: Dict[str, Any] = {
        "event_type": event_type,
        "request_id": request_id,
        "company_id": company_id,
        "policy_version_id": version_id,
        "retrieval_mode": retrieval_mode,
        "num_candidates": num_candidates,
        "citation_ids": citation_ids,
        "llm_calls": llm_calls,
        "status": status,
        "timing_ms": timing_ms,
    }
    if extra:
        payload["extra"] = extra
    # Filter out None values
    clean_payload = {k: v for k, v in payload.items() if v is not None}
    logger.info(json.dumps(clean_payload))


class Timer:
    def __init__(self) -> None:
        self.start_time = time.perf_counter()
        self.elapsed_ms: float = 0.0

    def stop(self) -> float:
        self.elapsed_ms = (time.perf_counter() - self.start_time) * 1000.0
        return self.elapsed_ms
