import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parent.parent / "scripts" / "pr_review_ab.py"
SPEC = importlib.util.spec_from_file_location("pr_review_ab", MODULE_PATH)
pr_review_ab = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(pr_review_ab)


def make_result(name: str, *, input_tokens: int, output_tokens: int, command_count: int):
    return pr_review_ab.VariantResult(
        name=name,
        prompt="prompt",
        command=["codex"],
        returncode=0,
        thread_id=None,
        input_tokens=input_tokens,
        cached_input_tokens=0,
        output_tokens=output_tokens,
        command_count=command_count,
        command_counts_by_prog={},
        wumw_command_count=0,
        raw_events_path=Path("/tmp/events.jsonl"),
        final_message_path=Path("/tmp/final.md"),
        final_message="",
        wumw_session_path=None,
        wumw_savings=None,
    )


def test_summarize_trial_results_computes_treatment_vs_baseline_deltas():
    summary = pr_review_ab.summarize_trial_results(
        [
            make_result(
                "baseline_no_wumw",
                input_tokens=1000,
                output_tokens=1100,
                command_count=125,
            ),
            make_result(
                "treatment_with_wumw",
                input_tokens=635,
                output_tokens=780,
                command_count=77,
            ),
        ]
    )

    assert summary["metrics"]["input_tokens"]["delta_pct"] == pytest.approx(-36.5)
    assert summary["metrics"]["output_tokens"]["delta_pct"] == pytest.approx(-29.0909, rel=1e-4)
    assert summary["metrics"]["command_count"]["delta_pct"] == pytest.approx(-38.4)


def test_aggregate_trial_summaries_reports_mean_median_and_sample_stdev():
    trial_summaries = [
        {
            "metrics": {
                "input_tokens": {"delta_pct": -36.5},
                "output_tokens": {"delta_pct": -29.1},
                "command_count": {"delta_pct": -38.4},
            }
        },
        {
            "metrics": {
                "input_tokens": {"delta_pct": -3.0},
                "output_tokens": {"delta_pct": 43.3},
                "command_count": {"delta_pct": 16.2},
            }
        },
        {
            "metrics": {
                "input_tokens": {"delta_pct": -0.9},
                "output_tokens": {"delta_pct": 2.4},
                "command_count": {"delta_pct": -3.2},
            }
        },
    ]

    aggregate = pr_review_ab.aggregate_trial_summaries(trial_summaries)

    assert aggregate["input_tokens"]["mean_pct"] == pytest.approx(-13.4667, rel=1e-4)
    assert aggregate["input_tokens"]["median_pct"] == pytest.approx(-3.0)
    assert aggregate["input_tokens"]["stdev_pct_points"] == pytest.approx(19.9751, rel=1e-4)

    assert aggregate["output_tokens"]["mean_pct"] == pytest.approx(5.5333, rel=1e-4)
    assert aggregate["output_tokens"]["median_pct"] == pytest.approx(2.4)
    assert aggregate["output_tokens"]["stdev_pct_points"] == pytest.approx(36.3016, rel=1e-4)

    assert aggregate["command_count"]["mean_pct"] == pytest.approx(-8.4667, rel=1e-4)
    assert aggregate["command_count"]["median_pct"] == pytest.approx(-3.2)
    assert aggregate["command_count"]["stdev_pct_points"] == pytest.approx(27.6784, rel=1e-4)


def test_delta_pct_returns_none_for_zero_or_missing_baseline():
    assert pr_review_ab.delta_pct(0, 10) is None
    assert pr_review_ab.delta_pct(None, 10) is None
    assert pr_review_ab.delta_pct(10, None) is None
