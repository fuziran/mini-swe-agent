"""Repair a reproducible pytest failure and export a verified patch."""

import json
import re
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import typer

from minisweagent.agents import get_agent
from minisweagent.config import get_config_from_spec
from minisweagent.environments import get_environment
from minisweagent.models import get_model

app = typer.Typer()
FAILED_TEST = re.compile(r"(?m)^FAILED (\S+::\S+)")


def _git(root: Path, *args: str, input: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, input=input, text=True, capture_output=True, check=True
    ).stdout.strip()


def _test(root: Path, image: str, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp",
            "--cpus", "2", "--memory", "2g", "-e", "PYTHONDONTWRITEBYTECODE=1",
            "--mount", f"type=bind,source={root},target=/workspace,readonly", "-w", "/workspace",
            image, "python", "-m", "pytest", "-q", "--color=no", "-p", "no:cacheprovider",
        ],
        text=True, capture_output=True, timeout=timeout,
    )


def _failed_tests(output: str) -> set[str]:
    return set(FAILED_TEST.findall(output))


def _allowed_changes(root: Path) -> list[str]:
    paths = _git(root, "diff", "HEAD", "--name-only", "--no-renames").splitlines()
    if (
        not paths
        or _git(root, "ls-files", "--others", "--exclude-standard")
        or any(not path.startswith("src/") or not path.endswith(".py") for path in paths)
    ):
        return []
    return paths


@app.command()
def main(
    repo: Path = typer.Option(..., help="Checkout of the failed commit."),
    workflow: str = typer.Option(..., help="Name of the failed workflow."),
    sha: str = typer.Option(..., help="Full SHA of the failed commit."),
    log: Path = typer.Option(..., help="Raw pytest log from the failed workflow."),
    output: Path = typer.Option(..., help="Directory for patch, logs, report, and trajectory."),
    model: str | None = typer.Option(None, help="Model name; otherwise use MSWEA_MODEL_NAME."),
    image: str = typer.Option("mini-swe-agent-repository:latest", help="Prebuilt test and agent image."),
    timeout: int = typer.Option(600, help="Seconds allowed for each test run and the agent."),
) -> None:
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=True)
    report: dict = {"workflow": workflow, "sha": sha}

    def stop(status: str) -> None:
        report.update(status=status, duration_seconds=round(time.monotonic() - started, 2))
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        if status != "verified":
            raise typer.Exit(1)

    if workflow != "Pytest" or len(sha) != 40 or _git(repo, "rev-parse", "HEAD") != sha:
        stop("invalid_input")
    incoming_failures = _failed_tests(log.read_text(errors="replace"))
    if not incoming_failures:
        stop("not_pytest_failure")

    with TemporaryDirectory(prefix="mini-ci-repair-") as temp:
        roots = {name: Path(temp) / name for name in ("baseline", "agent", "candidate")}
        for root in roots.values():
            subprocess.run(["git", "clone", "--quiet", "--no-local", str(repo), str(root)], check=True)
            _git(root, "checkout", "--detach", sha)

        baseline = _test(roots["baseline"], image, timeout)
        baseline_output = baseline.stdout + baseline.stderr
        (output / "baseline.log").write_text(baseline_output)
        report["baseline_failures"] = sorted(_failed_tests(baseline_output))
        if baseline.returncode == 0 or not incoming_failures.intersection(report["baseline_failures"]):
            stop("not_reproduced")

        config = get_config_from_spec("repository.yaml")
        config["environment"].update(root=roots["agent"], image=image)
        config["agent"].update(
            mode="yolo", confirm_exit=False, wall_time_limit_seconds=timeout,
            output_path=output / "trajectory.json",
        )
        if model:
            config["model"]["model_name"] = model
        environment = get_environment(config["environment"])
        agent = get_agent(get_model(config=config["model"]), environment, config["agent"])
        report["status"] = "agent_error"
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        try:
            result = agent.run(
                "Fix the reproducible pytest failure. Do not modify tests or configuration. "
                f"Failed workflow: {workflow}; commit: {sha}.\n"
                f"Failing tests: {', '.join(sorted(incoming_failures))}.\n"
                f"Original CI log (truncated):\n{log.read_text(errors='replace')[-12000:]}"
            )
        finally:
            environment.cleanup()
        report.update(agent_status=result.get("exit_status"), model_cost=agent.cost, model_calls=agent.n_calls)
        if result.get("exit_status") != "Submitted" or _git(roots["agent"], "rev-parse", "HEAD") != sha:
            stop("agent_incomplete")

        paths = _allowed_changes(roots["agent"])
        report["changed_files"] = paths
        if not paths:
            stop("invalid_changes")
        patch = _git(roots["agent"], "diff", "HEAD", "--binary", "--no-ext-diff") + "\n"
        (output / "patch.diff").write_text(patch)
        _git(roots["candidate"], "apply", "--check", "-", input=patch)
        _git(roots["candidate"], "apply", "-", input=patch)

        candidate = _test(roots["candidate"], image, timeout)
        (output / "candidate.log").write_text(candidate.stdout + candidate.stderr)
        if candidate.returncode:
            stop("candidate_failed")
        stop("verified")


if __name__ == "__main__":
    app()
