"""Run the frozen CI repair evaluation once per case."""

import hashlib
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

import typer

from minisweagent.run.ci_repair import _xml_failures, _xml_results

app = typer.Typer()
ROOT = Path(__file__).resolve().parent
MODEL = "deepseek/deepseek-flash"
INNER_TIMEOUT = 300
OUTER_TIMEOUT = 900


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True).stdout.strip()


def image_id() -> str:
    return json.loads(
        subprocess.run(
            ["docker", "image", "inspect", "sha256:89cd1a451dfa7bac8d24aedd4f40351ec62bfdef7a6069124a428167dd104e91"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
    )[0]["Id"]


def plan_for(repositories: Path, inputs: Path) -> dict:
    manifest = ROOT / "cases.jsonl"
    validation = json.loads((ROOT / "validation.json").read_text())
    cases = [json.loads(line) for line in manifest.read_text().splitlines()]
    if len(cases) != 20 or len({case["bug_sha"] for case in cases}) != 20:
        raise ValueError("The frozen manifest must contain 20 distinct SHAs")
    if image_id() != validation["image_id"]:
        raise ValueError("The frozen Docker image is unavailable or has changed")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("DEEPSEEK_API_KEY is not configured")
    if {entry["case_id"]: entry["matches_design"] for entry in validation["cases"]} != {
        case["case_id"]: True for case in cases
    }:
        raise ValueError("Case validation does not match the frozen manifest")
    records = []
    for case in cases:
        repo = repositories / case["case_id"]
        source = inputs / case["case_id"]
        if git(repo, "rev-parse", "HEAD") != case["bug_sha"] or git(repo, "rev-list", "--all", "--count") != "1":
            raise ValueError(f"Case repository SHA or history changed: {case['case_id']}")
        if git(repo, "status", "--porcelain"):
            raise ValueError(f"Case repository is dirty: {case['case_id']}")
        failures, errors = _xml_failures(source / "original.xml")
        if failures != set(case["expected_failed_nodes"]) or errors != (case["case_id"] == "E20"):
            raise ValueError(f"Original pytest XML changed: {case['case_id']}")
        records.append(
            {
                **case,
                "repository": str(repo),
                "original_log_sha256": digest(source / "original.log"),
                "original_xml_sha256": digest(source / "original.xml"),
            }
        )
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "agent_sha": git(ROOT.parent, "rev-parse", "HEAD"),
        "manifest_sha256": digest(manifest),
        "runner_sha256": digest(Path(__file__)),
        "config_sha256": digest(ROOT / "agent_config.yaml"),
        "image_id": validation["image_id"],
        "model": MODEL,
        "inner_timeout_seconds": INNER_TIMEOUT,
        "outer_timeout_seconds": OUTER_TIMEOUT,
        "cases": records,
    }


@app.command()
def prepare(repositories: Path, inputs: Path, output: Path) -> None:
    repositories, inputs, output = repositories.resolve(), inputs.resolve(), output.resolve()
    if output.exists():
        raise ValueError(f"Output already exists: {output}")
    if git(ROOT.parent, "status", "--porcelain"):
        raise ValueError("Commit the evaluation runner before preparing a batch")
    plan = plan_for(repositories, inputs)
    output.mkdir(parents=True)
    for case in plan["cases"]:
        case_output = output / case["case_id"]
        case_output.mkdir()
        source = inputs / case["case_id"]
        shutil.copy2(source / "original.log", case_output / "original.log")
        shutil.copy2(source / "original.xml", case_output / "original.xml")
    (output / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    typer.echo(f"Prepared {len(plan['cases'])} frozen cases at {output}; no model calls made")


def regression(case_output: Path, report: dict, failed_nodes: set[str]) -> bool | None:
    if report.get("target_returncode") != 0 or report.get("candidate_returncode") not in {0, 1}:
        return None
    try:
        baseline = _xml_results(case_output / "baseline.xml", report["baseline_returncode"])
        candidate = _xml_results(case_output / "candidate.xml", report["candidate_returncode"])
    except (OSError, ValueError, ElementTree.ParseError):
        return None
    if any(candidate.get(node) != "passed" for node in failed_nodes):
        return None
    return any(candidate.get(node) != "passed" for node, status in baseline.items() if status == "passed")


def result_for(case: dict, case_output: Path, duration: float, returncode: int | None, timed_out: bool) -> dict:
    report_path = case_output / "report.json"
    try:
        report = json.loads(report_path.read_text()) if report_path.is_file() else {}
    except json.JSONDecodeError:
        report = {}
        invalid_report = True
    else:
        invalid_report = False
    if timed_out:
        status = "outer_timeout"
    elif invalid_report:
        status = "invalid_report"
    else:
        status = report.get("status", "missing_report")
    expected = set(case["expected_failed_nodes"])
    reproduced = bool(
        report.get("baseline_returncode") == 1
        and set(report.get("baseline_failures", [])) == expected
        and report.get("baseline_repeat_returncode") == 1
        and set(report.get("baseline_repeat_failures", [])) == expected
        and status not in {"not_reproduced", "unstable"}
    )
    return {
        "case_id": case["case_id"],
        "bug_sha": case["bug_sha"],
        "category": case["category"],
        "status": status,
        "entry_returncode": returncode,
        "reproduced": reproduced,
        "candidate_submitted": report.get("agent_status") == "Submitted",
        "valid_fix": None if status == "verified" else False,
        "regression": regression(case_output, report, expected),
        "failure_reason": {
            "outer_timeout": "Evaluation exceeded 900 seconds",
            "invalid_report": "Repair entry wrote invalid JSON",
            "missing_report": "Repair entry did not write a report",
        }.get(status, report.get("reason")),
        "duration_seconds": round(duration, 2),
        "model_cost_usd": report.get("model_cost"),
        "model_calls": report.get("model_calls"),
        "pr_url": None,
    }


def write_summary(output: Path, cases: list[dict]) -> None:
    results = [
        json.loads((output / case["case_id"] / "result.json").read_text())
        for case in cases
        if (output / case["case_id"] / "result.json").is_file()
    ]
    costs = [result["model_cost_usd"] for result in results if result["model_cost_usd"] is not None]
    durations = [result["duration_seconds"] for result in results]
    fixed = sum(result["valid_fix"] is True for result in results)
    summary = {
        "denominator": 20,
        "completed": len(results),
        "reproduced": sum(result["reproduced"] for result in results),
        "valid_fix": fixed,
        "pending_review": sum(result["valid_fix"] is None for result in results),
        "regression": sum(result["regression"] is True for result in results),
        "regression_unassessed": sum(result["regression"] is None for result in results),
        "statuses": {
            status: sum(result["status"] == status for result in results)
            for status in sorted({result["status"] for result in results})
        },
        "model_cost_estimate_usd": round(sum(costs), 9),
        "model_cost_unknown_cases": len(results) - len(costs),
        "cost_per_valid_fix_usd": round(sum(costs) / fixed, 9) if fixed and len(costs) == len(results) else None,
        "median_duration_seconds": statistics.median(durations) if durations else None,
        "max_duration_seconds": max(durations, default=None),
    }
    if len(results) == 20 and not summary["pending_review"]:
        summary.update(
            reproduction_rate=summary["reproduced"] / 20,
            valid_fix_rate=fixed / 20,
            regression_rate=summary["regression"] / 20,
            valid_fix_given_reproduced=fixed / summary["reproduced"] if summary["reproduced"] else None,
        )
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


@app.command()
def execute(output: Path, limit: int = 20) -> None:
    output = output.resolve()
    plan = json.loads((output / "plan.json").read_text())
    if limit < 1 or plan["runner_sha256"] != digest(Path(__file__)):
        raise ValueError("Invalid limit or evaluation runner changed")
    if (
        plan["agent_sha"] != git(ROOT.parent, "rev-parse", "HEAD")
        or plan["manifest_sha256"] != digest(ROOT / "cases.jsonl")
        or plan["config_sha256"] != digest(ROOT / "agent_config.yaml")
        or plan["image_id"] != image_id()
    ):
        raise ValueError("The frozen code, manifest, config, or image changed")
    pending = [case for case in plan["cases"] if not (output / case["case_id"] / "result.json").is_file()]
    for case in pending[:limit]:
        case_output = output / case["case_id"]
        if (case_output / "attempt_started.json").exists():
            raise ValueError(f"Interrupted attempt requires audit before continuing: {case['case_id']}")
        if git(Path(case["repository"]), "rev-parse", "HEAD") != case["bug_sha"]:
            raise ValueError(f"Case SHA changed: {case['case_id']}")
        if (
            digest(case_output / "original.log") != case["original_log_sha256"]
            or digest(case_output / "original.xml") != case["original_xml_sha256"]
        ):
            raise ValueError(f"Original input changed: {case['case_id']}")
        (case_output / "runtime.json").write_text(
            json.dumps(
                {
                    key: plan[key]
                    for key in (
                        "agent_sha",
                        "manifest_sha256",
                        "runner_sha256",
                        "config_sha256",
                        "image_id",
                        "model",
                        "inner_timeout_seconds",
                        "outer_timeout_seconds",
                    )
                },
                indent=2,
            )
            + "\n"
        )
        (case_output / "attempt_started.json").write_text(
            json.dumps({"started_at_utc": datetime.now(timezone.utc).isoformat()}) + "\n"
        )
        command = [
            sys.executable,
            "-m",
            "minisweagent.run.ci_repair",
            "--repo",
            case["repository"],
            "--workflow",
            "Pytest",
            "--sha",
            case["bug_sha"],
            "--log",
            str(case_output / "original.log"),
            "--xml",
            str(case_output / "original.xml"),
            "--output",
            str(case_output),
            "--model",
            MODEL,
            "--config",
            str(ROOT / "agent_config.yaml"),
            "--image",
            plan["image_id"],
            "--timeout",
            str(INNER_TIMEOUT),
        ]
        started = time.monotonic()
        process = subprocess.Popen(
            command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            log, _ = process.communicate(timeout=OUTER_TIMEOUT)
            returncode, timed_out = process.returncode, False
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            log, _ = process.communicate()
            returncode, timed_out = None, True
        (case_output / "entry.log").write_text(log)
        result = result_for(case, case_output, time.monotonic() - started, returncode, timed_out)
        (case_output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        typer.echo(
            f"{case['case_id']}: {result['status']} ({result['duration_seconds']}s, {result['model_calls']} calls)"
        )
    write_summary(output, plan["cases"])


@app.command()
def summarize(output: Path) -> None:
    output = output.resolve()
    write_summary(output, json.loads((output / "plan.json").read_text())["cases"])
    typer.echo((output / "summary.json").read_text())


@app.command()
def review(output: Path, case_id: str, valid: bool, reason: str = "") -> None:
    output = output.resolve()
    plan = json.loads((output / "plan.json").read_text())
    if case_id not in {case["case_id"] for case in plan["cases"]}:
        raise ValueError(f"Unknown case: {case_id}")
    case_output = output / case_id
    if (case_output / "review.json").exists():
        raise ValueError(f"Case already reviewed: {case_id}")
    result_path = case_output / "result.json"
    result = json.loads(result_path.read_text())
    if result["status"] != "verified" or result["valid_fix"] is not None:
        raise ValueError("Only unreviewed verified candidates need manual review")
    if not valid and not reason:
        raise ValueError("Invalid fixes require a concrete reason")
    review_record = {
        "case_id": case_id,
        "valid_fix": valid,
        "reason": reason or None,
        "patch_sha256": digest(case_output / "patch.diff"),
        "report_sha256": digest(case_output / "report.json"),
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (case_output / "review.json").write_text(json.dumps(review_record, indent=2) + "\n")
    result["valid_fix"] = valid
    if not valid:
        result["failure_reason"] = reason
    result_path.write_text(json.dumps(result, indent=2) + "\n")
    write_summary(output, plan["cases"])


if __name__ == "__main__":
    app()
