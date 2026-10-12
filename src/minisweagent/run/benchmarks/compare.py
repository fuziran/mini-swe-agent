"""Compare two sets of agent trajectories on the same instances."""

import json
from pathlib import Path

import typer


def load_trajectories(directory: Path) -> dict[str, dict]:
    trajectories = {}
    for path in directory.rglob("*.traj.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        instance_id = data.get("instance_id", path.name.removesuffix(".traj.json"))
        if instance_id in trajectories:
            raise ValueError(f"Duplicate trajectory for {instance_id}")
        trajectories[instance_id] = data
    return trajectories


def trajectory_metrics(data: dict) -> dict:
    messages = data.get("messages", [])
    timestamps = [message.get("extra", {}).get("timestamp") for message in messages]
    timestamps = [stamp for stamp in timestamps if isinstance(stamp, (int, float))]
    usage = [message.get("extra", {}).get("response", {}) for message in messages]
    usage = [response.get("usage") or {} for response in usage if isinstance(response, dict)]
    return {
        "submitted": data.get("info", {}).get("exit_status") == "Submitted",
        "cost": data.get("info", {}).get("model_stats", {}).get("instance_cost", 0),
        "api_calls": data.get("info", {}).get("model_stats", {}).get("api_calls", 0),
        "prompt_tokens": sum(item.get("prompt_tokens", 0) or 0 for item in usage),
        "completion_tokens": sum(item.get("completion_tokens", 0) or 0 for item in usage),
        "tool_errors": sum(message.get("extra", {}).get("returncode") not in (None, 0) for message in messages),
        "model_latency_seconds": sum(message.get("extra", {}).get("model_latency_seconds", 0) for message in messages),
        "tool_duration_seconds": sum(message.get("extra", {}).get("duration_seconds", 0) for message in messages),
        "duration_seconds": max(timestamps) - min(timestamps) if len(timestamps) > 1 else None,
    }


def compare_runs(
    baseline_dir: Path,
    candidate_dir: Path,
    baseline_grades: dict[str, bool] | None = None,
    candidate_grades: dict[str, bool] | None = None,
) -> dict:
    baseline = load_trajectories(baseline_dir)
    candidate = load_trajectories(candidate_dir)
    common = sorted(baseline.keys() & candidate.keys())
    if not common:
        raise ValueError("No paired trajectories found")

    def summarize(trajectories: dict, grades: dict[str, bool] | None) -> dict:
        metrics = [trajectory_metrics(trajectories[iid]) for iid in common]
        result = {
            "submitted_rate": sum(item["submitted"] for item in metrics) / len(common),
            "mean_cost": sum(item["cost"] for item in metrics) / len(common),
            "mean_api_calls": sum(item["api_calls"] for item in metrics) / len(common),
            "mean_prompt_tokens": sum(item["prompt_tokens"] for item in metrics) / len(common),
            "mean_completion_tokens": sum(item["completion_tokens"] for item in metrics) / len(common),
            "mean_tool_errors": sum(item["tool_errors"] for item in metrics) / len(common),
            "mean_model_latency_seconds": sum(item["model_latency_seconds"] for item in metrics) / len(common),
            "mean_tool_duration_seconds": sum(item["tool_duration_seconds"] for item in metrics) / len(common),
        }
        durations = [item["duration_seconds"] for item in metrics if item["duration_seconds"] is not None]
        result["mean_duration_seconds"] = sum(durations) / len(durations) if durations else None
        if grades is not None:
            missing = set(common) - grades.keys()
            if missing:
                raise ValueError(f"Missing grades for {sorted(missing)}")
            result["resolved_rate"] = sum(grades[iid] for iid in common) / len(common)
        return result

    result = {
        "paired_instances": len(common),
        "baseline_only": len(baseline.keys() - candidate.keys()),
        "candidate_only": len(candidate.keys() - baseline.keys()),
        "baseline": summarize(baseline, baseline_grades),
        "candidate": summarize(candidate, candidate_grades),
        "instances": {
            iid: {"baseline": trajectory_metrics(baseline[iid]), "candidate": trajectory_metrics(candidate[iid])}
            for iid in common
        },
    }
    if baseline_grades is not None and candidate_grades is not None:
        for iid in common:
            result["instances"][iid].update(
                {"baseline_resolved": baseline_grades[iid], "candidate_resolved": candidate_grades[iid]}
            )
    return result


def main(
    baseline_dir: Path,
    candidate_dir: Path,
    baseline_grades: Path | None = typer.Option(None, help="JSON mapping instance IDs to baseline judgments"),
    candidate_grades: Path | None = typer.Option(None, help="JSON mapping instance IDs to candidate judgments"),
) -> None:
    if (baseline_grades is None) != (candidate_grades is None):
        raise typer.BadParameter("Provide both grade files or neither")
    result = compare_runs(
        baseline_dir,
        candidate_dir,
        json.loads(baseline_grades.read_text(encoding="utf-8")) if baseline_grades else None,
        json.loads(candidate_grades.read_text(encoding="utf-8")) if candidate_grades else None,
    )
    typer.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    typer.run(main)
