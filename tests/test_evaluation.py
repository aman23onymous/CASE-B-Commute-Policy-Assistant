import json
from pathlib import Path
import pytest
from scripts.evaluate import evaluate_suite


def test_cases_jsonl_schema_and_minimum_count():
    root = Path(__file__).resolve().parent.parent
    cases_path = root / "evaluation/cases.jsonl"
    assert cases_path.exists(), "cases.jsonl must exist"

    with open(cases_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    assert len(lines) >= 10, f"Must have >= 10 cases, found {len(lines)}"
    assert len(lines) >= 18, f"Must have >= 18 cases for complete matrix, found {len(lines)}"

    required_fields = {"id", "input", "expected", "actual", "pass", "notes"}
    seen_ids = set()

    for line in lines:
        data = json.loads(line)
        # Check required fields
        assert required_fields.issubset(data.keys()), f"Case {data.get('id')} missing required fields"
        assert data["id"] not in seen_ids, f"Duplicate ID: {data['id']}"
        seen_ids.add(data["id"])

        # Check input structure
        inp = data["input"]
        assert "company_id" in inp
        assert "question" in inp
        assert inp["company_id"] in ("aurora", "bluepeak", "cedar")

        # Check expected structure
        exp = data["expected"]
        assert "status" in exp
        assert exp["status"] in ("answered", "not_covered")


@pytest.mark.asyncio
async def test_evaluator_execution_and_schema_preservation(tmp_path):
    root = Path(__file__).resolve().parent.parent
    cases_path = root / "evaluation/cases.jsonl"
    out_path = tmp_path / "test_results.jsonl"

    summary = await evaluate_suite(
        cases_path=cases_path,
        output_path=out_path,
        retrieval_mode="baseline",
        llm_provider_type="fake",
        reseed=False,
    )

    assert summary["total_cases"] >= 18
    assert summary["passing_cases"] == summary["total_cases"]
    assert summary["tenant_leaks"] == 0
    assert summary["version_leaks"] == 0
    assert out_path.exists()

    with open(out_path, "r", encoding="utf-8") as f:
        out_lines = [json.loads(line) for line in f if line.strip()]

    required_fields = {"id", "input", "expected", "actual", "pass", "notes"}
    for r in out_lines:
        assert required_fields.issubset(r.keys())
        assert r["actual"] is not None
        assert r["pass"] is True
