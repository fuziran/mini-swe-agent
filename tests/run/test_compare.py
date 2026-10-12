import json

import pytest

from minisweagent.run.benchmarks.compare import compare_runs


def test_compare_runs_pairs_instances_and_uses_external_grades(tmp_path):
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    for directory, cost, status in ((baseline, 2.0, "Submitted"), (candidate, 1.0, "LimitsExceeded")):
        (directory / "issue.traj.json").write_text(
            json.dumps(
                {
                    "instance_id": "issue",
                    "info": {"exit_status": status, "model_stats": {"instance_cost": cost, "api_calls": 2}},
                    "messages": [
                        {
                            "extra": {
                                "timestamp": 10,
                                "response": {"usage": {"prompt_tokens": 20, "completion_tokens": 5}},
                            }
                        },
                        {"extra": {"timestamp": 20, "returncode": 1}},
                        {"extra": {"returncode": -1, "exception_info": "action was not executed"}},
                    ],
                }
            )
        )
    report = compare_runs(baseline, candidate, {"issue": False}, {"issue": True})
    assert report["paired_instances"] == 1
    assert report["baseline"]["submitted_rate"] == 1
    assert report["candidate"]["submitted_rate"] == 0
    assert report["baseline"]["resolved_rate"] == 0
    assert report["candidate"]["resolved_rate"] == 1
    assert report["candidate"]["mean_cost"] == 1
    assert report["candidate"]["mean_prompt_tokens"] == 20
    assert report["candidate"]["mean_tool_errors"] == 1
    assert report["candidate"]["mean_duration_seconds"] == 10
    assert report["instances"]["issue"]["candidate_resolved"] is True
    with pytest.raises(ValueError, match="Missing grades"):
        compare_runs(baseline, candidate, {}, {"issue": True})
