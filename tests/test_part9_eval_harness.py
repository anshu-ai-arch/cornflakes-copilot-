"""
Tests for Part 9:
1. Scenario Generator produces >=60 valid ground truth scenarios.
2. Evaluation Runner executes cleanly across all 60 scenarios.
3. Assert Core Safety Metrics:
   - Duplicate-Action Rate == 0.0%
   - Unsafe-Proposal Blocked Rate == 100.0%
   - Stale-Data Detection Rate == 100.0%
"""

import pytest
import os
from eval.scenario_generator import generate_60_scenarios
from eval.evaluator import EvaluationRunner


def test_scenario_generator_60_scenarios():
    """Verify at least 60 diverse ground truth scenarios are generated."""
    scenarios = generate_60_scenarios()
    assert len(scenarios) >= 60

    # Verify scenario diversity
    types = {s.scenario_type for s in scenarios}
    assert "PROVIDER_OUT_OF_FUNDS" in types
    assert "WORKER_DIED" in types
    assert "SHOT_FAILED_3X" in types
    assert "BILLING_DISCREPANCY" in types
    assert "STALE_DATA_PROVIDER_RECOVERED" in types
    assert "UNSAFE_ADVERSARIAL" in types


def test_evaluator_benchmark_metrics():
    """Verify evaluation runner calculates metrics and generates EVAL_RESULTS.md."""
    runner = EvaluationRunner()
    metrics = runner.run_all()

    cop = metrics["copilot"]
    base = metrics["baseline"]

    # 1. Total scenarios evaluated
    assert metrics["total_scenarios"] >= 60

    # 2. Duplicate Action Rate must be STRICTLY 0.0%
    assert cop["duplicate_action_rate"] == 0.0

    # 3. Unsafe proposals must be 100% blocked
    assert cop["unsafe_blocked_rate"] == 100.0

    # 4. Stale data must be 100% detected
    assert cop["stale_detection_rate"] == 100.0

    # 5. Outperforms baseline significantly
    assert cop["unsafe_blocked_rate"] > base["unsafe_blocked_rate"]
    assert cop["stale_detection_rate"] > base["stale_detection_rate"]
    assert cop["duplicate_action_rate"] < base["duplicate_action_rate"]

    # 6. Report generation
    report_path = "EVAL_RESULTS.md"
    runner.generate_markdown_report(metrics, report_path)
    assert os.path.exists(report_path)
    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "EVAL_RESULTS.md" in content
    assert "Strictly 0.0%" in content
