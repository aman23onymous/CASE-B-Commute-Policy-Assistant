import argparse
import asyncio
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.answer import AnswerService, NOT_COVERED_TEXT
from app.config import settings
from app.db import get_db_connection
from app.demo_issuer import create_access_token
from app.embedder import get_embedder
from app.llm import get_llm_provider
from app.retrieval import Retriever
from app.schemas import Principal
from app.vector_store import ChromaVectorStore
from scripts.seed import seed_database


def calculate_percentile(values: List[float], percentile: float) -> float:
    """Calculate nearest-rank percentile."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    k = math.ceil(len(sorted_v) * percentile) - 1
    idx = max(0, min(k, len(sorted_v) - 1))
    return round(sorted_v[idx], 2)


async def evaluate_suite(
    cases_path: Path,
    output_path: Path,
    retrieval_mode: str = "baseline",
    llm_provider_type: str = "fake",
    force_fake_embedder: bool = False,
    reseed: bool = True,
) -> Dict[str, Any]:
    """
    Run deterministic evaluation suite against cases.jsonl.
    Spec §14.3.
    """
    if reseed:
        seed_database(reset=True, use_fake_embedder=force_fake_embedder)

    conn = get_db_connection(settings.SQLITE_PATH)
    vector_store = ChromaVectorStore(settings.CHROMA_PATH)
    embedder = get_embedder(force_fake=force_fake_embedder)
    retriever = Retriever(vector_store, embedder)
    llm = get_llm_provider(llm_provider_type)
    answer_service = AnswerService(conn, retriever, llm)

    with open(cases_path, "r", encoding="utf-8") as f:
        case_lines = [line.strip() for line in f if line.strip()]

    evaluated_records: List[Dict[str, Any]] = []
    passing_count = 0
    tenant_leaks = 0
    version_leaks = 0
    status_matches = 0
    valid_citations_count = 0
    retrieval_hits = 0

    expected_nc_count = 0
    actual_nc_count = 0
    true_nc_count = 0

    retrieval_times: List[float] = []
    total_times: List[float] = []

    print(f"\n=======================================================")
    print(f" COMMUTE POLICY ASSISTANT - EVALUATION RUN")
    print(f" Mode: {retrieval_mode} | Provider: {llm_provider_type}")
    print(f" Cases: {len(case_lines)} from {cases_path.name}")
    print(f"=======================================================\n")

    for line in case_lines:
        case = json.loads(line)
        case_id = case["id"]
        inp = case["input"]
        expected = case["expected"]
        notes = case.get("notes", "")

        company_id = inp["company_id"]
        question = inp["question"]
        expected_version = inp.get("policy_version", 1)

        # Harness creates authenticated principal
        principal = Principal(
            user_id=f"{company_id}_eval_user",
            company_id=company_id,
            role="employee",
            display_name=f"Eval User ({company_id})",
        )

        resp = await answer_service.ask(
            principal=principal,
            question=question,
            retrieval_mode=retrieval_mode,
        )

        retrieval_times.append(resp.timing_ms.retrieval)
        total_times.append(resp.timing_ms.total)

        actual_citations = [c.model_dump() for c in resp.citations]
        actual_record = {
            "status": resp.status,
            "answer": resp.answer,
            "citations": actual_citations,
            "timing_ms": resp.timing_ms.model_dump(),
        }

        # Scoring Assertions
        case_pass = True
        failure_reasons = []

        # 1. Status Check
        if resp.status == expected["status"]:
            status_matches += 1
        else:
            case_pass = False
            failure_reasons.append(f"Status mismatch: expected {expected['status']}, got {resp.status}")

        if expected["status"] == "not_covered":
            expected_nc_count += 1
            if resp.status == "not_covered":
                actual_nc_count += 1
                true_nc_count += 1
                # Must not cite any chunks
                if len(actual_citations) != 0:
                    case_pass = False
                    failure_reasons.append(f"not_covered response must have 0 citations, got {len(actual_citations)}")
            else:
                case_pass = False
                failure_reasons.append(f"Expected not_covered but got {resp.status}")
        else:
            # 2. Answered assertions
            if resp.status == "answered":
                # Citation count must be 1 to 3
                if not (1 <= len(actual_citations) <= 3):
                    case_pass = False
                    failure_reasons.append(f"Citation count {len(actual_citations)} not in [1, 3]")
                else:
                    valid_citations_count += 1

                # Tenant & Version isolation check
                for cit in actual_citations:
                    if cit["version"] != expected_version:
                        version_leaks += 1
                        case_pass = False
                        failure_reasons.append(f"Version leak: cited version {cit['version']} != {expected_version}")
                    # Citation document title / company check
                    if company_id == "aurora" and "BluePeak" in cit["document_title"]:
                        tenant_leaks += 1
                        case_pass = False
                        failure_reasons.append("Tenant leak: Aurora cited BluePeak")
                    if company_id == "bluepeak" and "Aurora" in cit["document_title"]:
                        tenant_leaks += 1
                        case_pass = False
                        failure_reasons.append("Tenant leak: BluePeak cited Aurora")

                # Retrieval Hit@3: check if expected sections were retrieved in citations
                expected_secs = expected.get("sections", [])
                cited_secs = [c["section_id"] for c in actual_citations]
                if any(es in cited_secs for es in expected_secs):
                    retrieval_hits += 1
                else:
                    case_pass = False
                    failure_reasons.append(f"Retrieval miss: expected section {expected_secs}, cited {cited_secs}")

                # Fact check: verify answer contains expected facts
                expected_facts = expected.get("answer_facts", [])
                for ef in expected_facts:
                    if ef.lower() not in resp.answer.lower():
                        case_pass = False
                        failure_reasons.append(f"Fact missing: '{ef}' not found in answer")

        if case_pass:
            passing_count += 1
            eval_notes = notes
        else:
            eval_notes = f"{notes} | FAIL: {'; '.join(failure_reasons)}"

        status_symbol = "PASS" if case_pass else "FAIL"
        print(f"[{status_symbol}] {case_id} ({company_id}): {question[:45]:<45} -> {resp.status} ({resp.timing_ms.total}ms)")

        # Exact 6-field JSONL schema (Spec §14.1 & §14.3)
        evaluated_records.append({
            "id": case_id,
            "input": inp,
            "expected": expected,
            "actual": actual_record,
            "pass": case_pass,
            "notes": eval_notes,
        })

    conn.close()

    total_cases = len(case_lines)
    answer_pass_rate = round(passing_count / max(1, total_cases), 4)
    status_accuracy = round(status_matches / max(1, total_cases), 4)
    citation_validity_rate = round(valid_citations_count / max(1, total_cases - expected_nc_count), 4)
    retrieval_hit_at_3 = round(retrieval_hits / max(1, total_cases - expected_nc_count), 4)
    nc_precision = round(true_nc_count / max(1, actual_nc_count), 4) if actual_nc_count > 0 else 1.0
    nc_recall = round(true_nc_count / max(1, expected_nc_count), 4) if expected_nc_count > 0 else 1.0

    p50_retrieval = calculate_percentile(retrieval_times, 0.50)
    p95_retrieval = calculate_percentile(retrieval_times, 0.95)
    p50_total = calculate_percentile(total_times, 0.50)
    p95_total = calculate_percentile(total_times, 0.95)

    summary = {
        "mode": retrieval_mode,
        "provider": llm_provider_type,
        "total_cases": total_cases,
        "passing_cases": passing_count,
        "answer_pass_rate": answer_pass_rate,
        "status_accuracy": status_accuracy,
        "citation_validity_rate": citation_validity_rate,
        "retrieval_hit_at_3": retrieval_hit_at_3,
        "tenant_leaks": tenant_leaks,
        "version_leaks": version_leaks,
        "not_covered_precision": nc_precision,
        "not_covered_recall": nc_recall,
        "p50_retrieval_ms": p50_retrieval,
        "p95_retrieval_ms": p95_retrieval,
        "p50_end_to_end_ms": p50_total,
        "p95_end_to_end_ms": p95_total,
    }

    # Write output JSONL
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as out_f:
        for rec in evaluated_records:
            out_f.write(json.dumps(rec) + "\n")

    print("\n-------------------------------------------------------")
    print(" SUMMARY METRICS:")
    print(f" Pass Rate:              {answer_pass_rate * 100:.1f}% ({passing_count}/{total_cases})")
    print(f" Status Accuracy:        {status_accuracy * 100:.1f}%")
    print(f" Citation Validity:      {citation_validity_rate * 100:.1f}%")
    print(f" Retrieval Hit@3:        {retrieval_hit_at_3 * 100:.1f}%")
    print(f" Tenant Leaks:           {tenant_leaks}")
    print(f" Version Leaks:          {version_leaks}")
    print(f" Not-Covered Precision:  {nc_precision * 100:.1f}%")
    print(f" Not-Covered Recall:     {nc_recall * 100:.1f}%")
    print(f" Latency Retrieval (p50/p95): {p50_retrieval}ms / {p95_retrieval}ms")
    print(f" Latency Total (p50/p95):     {p50_total}ms / {p95_total}ms")
    print("-------------------------------------------------------\n")
    print(f"[EVAL] Results saved to {output_path}")

    # Critical regressions exit nonzero
    if tenant_leaks > 0 or version_leaks > 0 or answer_pass_rate < 0.90:
        print("[EVAL] Critical regressions encountered!")
        sys.exit(1)

    return summary


def main():
    parser = argparse.ArgumentParser(description="Evaluate Commute Policy Assistant on fixed JSONL suite")
    parser.add_argument("--cases", default="evaluation/cases.jsonl", help="Path to input test cases JSONL")
    parser.add_argument("--mode", choices=["baseline", "hybrid"], default="baseline", help="Retrieval mode")
    parser.add_argument("--provider", choices=["fake", "real", "groq", "gemini"], default="fake", help="LLM provider")
    parser.add_argument("--output", default=None, help="Output results JSONL path")
    parser.add_argument("--fake-embedder", action="store_true", help="Force deterministic fake embedder")
    parser.add_argument("--no-seed", action="store_true", help="Do not reseed DB before evaluation")

    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    cases_file = root / args.cases

    out_file = Path(args.output) if args.output else root / f"evaluation/results_{args.mode}.jsonl"

    asyncio.run(
        evaluate_suite(
            cases_path=cases_file,
            output_path=out_file,
            retrieval_mode=args.mode,
            llm_provider_type=args.provider,
            force_fake_embedder=args.fake_embedder,
            reseed=not args.no_seed,
        )
    )


if __name__ == "__main__":
    main()
