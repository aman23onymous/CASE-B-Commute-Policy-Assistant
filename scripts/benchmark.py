import argparse
import asyncio
import math
import os
import platform
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.answer import AnswerService
from app.config import settings
from app.db import get_db_connection
from app.embedder import get_embedder
from app.llm import get_llm_provider
from app.retrieval import Retriever
from app.schemas import Principal
from app.vector_store import ChromaVectorStore
from scripts.seed import seed_database

BENCHMARK_PROMPTS = [
    ("aurora", "When is a security escort needed for a woman's drop?"),
    ("bluepeak", "When is a security escort needed for a woman's drop?"),
    ("cedar", "When is a security escort needed for a woman's drop?"),
    ("aurora", "Can I book a cab this Saturday?"),
    ("bluepeak", "Can I book a cab this Saturday?"),
    ("cedar", "Can I book a cab this Saturday?"),
    ("aurora", "Who approves an airport cab ride?"),
    ("bluepeak", "Who approves an airport cab ride?"),
    ("cedar", "Who approves an airport cab ride?"),
    ("aurora", "What is the penalty for missing a booked cab?"),
    ("bluepeak", "What is the penalty for missing a booked cab?"),
    ("cedar", "What is the penalty for missing a booked cab?"),
    ("aurora", "Can I bring my pet dog in the commute cab?"),
    ("bluepeak", "What is the compensation for lost luggage?"),
    ("aurora", "By what time must I finalize my roster commute request?"),
    ("bluepeak", "How many hours before pickup must I book a cab?"),
    ("cedar", "What is the reservation deadline for weekend shuttles?"),
    ("aurora", "What does BluePeak say about escort after 10 PM?"),
]


def percentile(vals: List[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = math.ceil(len(s) * p) - 1
    return round(s[max(0, min(k, len(s) - 1))], 2)


async def run_benchmark(
    provider_type: str = "fake",
    samples: int = 36,
    warmup_count: int = 3,
    retrieval_mode: str = "baseline",
    offline_mode: bool = False,
):
    print("=======================================================")
    print(" COMMUTE POLICY ASSISTANT - PERFORMANCE BENCHMARK")
    print("=======================================================")

    seed_database(reset=False)
    conn = get_db_connection(settings.SQLITE_PATH)
    vector_store = ChromaVectorStore(settings.CHROMA_PATH)
    embedder = get_embedder()
    retriever = Retriever(vector_store, embedder)

    effective_provider = "fake" if offline_mode else provider_type
    llm = get_llm_provider(effective_provider)
    answer_service = AnswerService(conn, retriever, llm)

    # System and runtime specs
    os_info = f"{platform.system()} {platform.release()} ({platform.machine()})"
    python_ver = platform.python_version()
    cpu_info = platform.processor() or "Multi-core CPU"

    print(f" Timestamp:      {datetime.now(timezone.utc).isoformat()}")
    print(f" Environment:    {os_info} | Python {python_ver}")
    print(f" CPU:            {cpu_info}")
    print(f" Embedding:      {settings.EMBEDDING_MODEL}")
    print(f" LLM Provider:   {effective_provider} ({settings.GROQ_MODEL if effective_provider == 'groq' else settings.GEMINI_MODEL if effective_provider == 'gemini' else 'Deterministic Simulation'})")
    print(f" Mode:           {retrieval_mode}")
    print(f" Offline Mode:   {offline_mode} ({'NOT evidence for real-LLM SLO' if offline_mode else 'Real API'})")
    print(f" Warmup / Runs:  {warmup_count} / {samples}")
    print("-------------------------------------------------------")

    # Warmup
    print("[BENCHMARK] Warming up embedding and provider pipelines...")
    for i in range(warmup_count):
        comp, q = BENCHMARK_PROMPTS[i % len(BENCHMARK_PROMPTS)]
        p = Principal(user_id=f"{comp}_user", company_id=comp, role="employee", display_name="Warmup")
        _ = await answer_service.ask(p, q, retrieval_mode=retrieval_mode)

    # Benchmark Execution
    print(f"[BENCHMARK] Executing {samples} sequential timed requests...")
    retrieval_latencies: List[float] = []
    llm_latencies: List[float] = []
    total_latencies: List[float] = []
    failures = 0
    budget_violations = 0
    citation_violations = 0

    start_wall = time.perf_counter()
    for idx in range(samples):
        comp, q = BENCHMARK_PROMPTS[idx % len(BENCHMARK_PROMPTS)]
        p = Principal(user_id=f"{comp}_user", company_id=comp, role="employee", display_name=f"Bench User {idx}")
        try:
            resp = await answer_service.ask(p, q, retrieval_mode=retrieval_mode)
            retrieval_latencies.append(resp.timing_ms.retrieval)
            llm_latencies.append(resp.timing_ms.llm)
            total_latencies.append(resp.timing_ms.total)

            if len(resp.citations) > 3:
                citation_violations += 1
        except Exception as e:
            failures += 1

    total_wall_sec = time.perf_counter() - start_wall
    conn.close()

    # Metrics
    successful_count = len(total_latencies)
    p50_total = percentile(total_latencies, 0.50)
    p95_total = percentile(total_latencies, 0.95)
    p50_retrieval = percentile(retrieval_latencies, 0.50)
    p95_retrieval = percentile(retrieval_latencies, 0.95)
    p50_llm = percentile(llm_latencies, 0.50)
    p95_llm = percentile(llm_latencies, 0.95)

    under_3s_count = sum(1 for t in total_latencies if t <= 3000.0)
    pct_under_3s = (under_3s_count / max(1, successful_count)) * 100.0

    min_total = round(min(total_latencies), 2) if total_latencies else 0.0
    max_total = round(max(total_latencies), 2) if total_latencies else 0.0

    slo_target_ms = 3000.0
    slo_passed = (p95_total <= slo_target_ms) and (failures == 0)

    print("\n=======================================================")
    print(" BENCHMARK RESULTS")
    print("=======================================================")
    print(f" Total Requests:         {samples}")
    print(f" Successful Requests:    {successful_count}")
    print(f" Failures / Timeouts:    {failures}")
    print(f" Budget Violations:      {budget_violations} (>1 LLM call)")
    print(f" Citation Violations:    {citation_violations} (>3 citations)")
    print(f" Total Wall Time:        {total_wall_sec:.2f} s")
    print(f" End-to-End Latency Min: {min_total} ms")
    print(f" End-to-End Latency Max: {max_total} ms")
    print(f" End-to-End p50:         {p50_total} ms")
    print(f" End-to-End p95:         {p95_total} ms")
    print(f" Retrieval Latency p95:  {p95_retrieval} ms")
    print(f" LLM Latency p95:        {p95_llm} ms")
    print(f" Queries <= 3000 ms:     {pct_under_3s:.1f}%")
    print(f" Target p95 <= 3,000 ms: {'PASSED' if slo_passed else 'DID NOT MEET TARGET'}")
    print("=======================================================\n")

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": effective_provider,
        "offline_mode": offline_mode,
        "sample_size": samples,
        "failures": failures,
        "p50_ms": p50_total,
        "p95_ms": p95_total,
        "p95_retrieval_ms": p95_retrieval,
        "p95_llm_ms": p95_llm,
        "pct_under_3s": pct_under_3s,
        "slo_passed": slo_passed,
    }


def main():
    parser = argparse.ArgumentParser(description="Performance benchmark for Commute Policy Assistant")
    parser.add_argument("--provider", choices=["fake", "real", "groq", "gemini"], default="fake")
    parser.add_argument("--samples", type=int, default=36, help="Number of benchmark queries")
    parser.add_argument("--warmup", type=int, default=3, help="Warmup queries")
    parser.add_argument("--mode", choices=["baseline", "hybrid"], default="baseline")
    parser.add_argument("--offline", action="store_true", help="Offline mode using fake provider")

    args = parser.parse_args()

    asyncio.run(
        run_benchmark(
            provider_type=args.provider,
            samples=args.samples,
            warmup_count=args.warmup,
            retrieval_mode=args.mode,
            offline_mode=args.offline or (args.provider == "fake"),
        )
    )


if __name__ == "__main__":
    main()
