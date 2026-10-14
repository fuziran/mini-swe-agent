"""Repair a reproducible pytest failure and export a verified patch."""

import json
import re
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from xml.etree import ElementTree

import typer

from minisweagent.agents import get_agent
from minisweagent.config import get_config_from_spec
from minisweagent.environments import get_environment
from minisweagent.models import get_model

app = typer.Typer()
FAILED_TEST = re.compile(r"(?m)^FAILED (\S+::\S+)")
COLLECTION_ERROR = re.compile(r"(?m)^(?:ERROR collecting |ImportError:|ModuleNotFoundError:)")


def _git(root: Path, *args: str, input: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, input=input, text=True, capture_output=True, check=True
    ).stdout.strip()


def _test(root: Path, image: str, timeout: int, tests: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp",
            "--cpus", "2", "--memory", "2g", "-e", "PYTHONDONTWRITEBYTECODE=1",
            "--mount", f"type=bind,source={root},target=/workspace,readonly", "-w", "/workspace",
            image, "python", "-m", "pytest", "-q", "--color=no", "-p", "no:cacheprovider", *(tests or []),
        ],
        text=True, capture_output=True, timeout=timeout,
    )


def _failed_tests(output: str) -> set[str]:
    return set(FAILED_TEST.findall(output))


def _xml_failures(path: Path) -> tuple[set[str], bool]:
    root = ElementTree.parse(path).getroot()
    if root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("Expected pytest testsuites or testsuite XML")
    failures: set[str] = set()
    errors = False
    for case in root.iter("testcase"):
        errors |= case.find("error") is not None
        if case.find("failure") is None:
            continue
        parts = case.attrib.get("classname", "").split(".")
        modules = [index for index, part in enumerate(parts) if part.startswith("test_") or part.endswith("_test")]
        name = case.attrib.get("name", "")
        if not modules or not name.startswith("test_"):
            raise ValueError("Cannot map pytest XML failure to a test node")
        index = modules[-1]
        failures.add(f"{'/'.join(parts[:index + 1])}.py::{'::'.join((*parts[index + 1:], name))}")
    return failures, errors


def _matches_failures(result: subprocess.CompletedProcess[str], expected: set[str]) -> bool:
    return result.returncode == 1 and _failed_tests(result.stdout + result.stderr) == expected


def _execution_error(result: subprocess.CompletedProcess[str]) -> bool:
    return result.returncode not in {0, 1} or (
        result.returncode == 1 and not _failed_tests(result.stdout + result.stderr)
    )


def _test_status(result: subprocess.CompletedProcess[str], failed_status: str) -> str | None:
    if _execution_error(result):
        return "execution_error"
    return failed_status if result.returncode else None


def _allowed_changes(root: Path) -> list[str]:
    entries = _git(root, "diff", "HEAD", "--raw", "--no-renames", "-z").split("\0")
    entries.pop()
    if not entries or len(entries) % 2 or _git(root, "ls-files", "--others", "--exclude-standard"):
        return []
    paths = []
    for metadata, path in zip(entries[::2], entries[1::2]):
        fields = metadata.split()
        if (
            len(fields) != 5 or fields[-1] != "M"
            or fields[0][1:] != fields[1] or fields[1] not in {"100644", "100755"}
            or not path.startswith("src/") or not path.endswith(".py")
        ):
            return []
        paths.append(path)
    return paths


@app.command()
def main(
    repo: Path = typer.Option(..., help="Checkout of the failed commit."),
    workflow: str = typer.Option(..., help="Name of the failed workflow."),
    run_id: int | None = typer.Option(None, help="GitHub Actions run ID of the failed workflow."),
    sha: str = typer.Option(..., help="Full SHA of the failed commit."),
    log: Path = typer.Option(..., help="Raw pytest log from the failed workflow."),
    xml: Path | None = typer.Option(None, help="Pytest XML from the failed workflow, when available."),
    output: Path = typer.Option(..., help="Directory for patch, logs, report, and trajectory."),
    model: str | None = typer.Option(None, help="Model name; otherwise use MSWEA_MODEL_NAME."),
    image: str = typer.Option("mini-swe-agent-repository:latest", help="Prebuilt test and agent image."),
    timeout: int = typer.Option(600, help="Seconds allowed for each test run and the agent."),
) -> None:
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"workflow": workflow, "sha": sha}
    if run_id is not None:
        report["run_id"] = run_id
    stage = "input"

    def reject(status: str, reason: str) -> str:
        report.update(reason=reason, failed_stage=stage)
        return status

    def repair() -> str:
        nonlocal stage
        if timeout <= 0:
            return reject("invalid_input", "Timeout must be positive")
        if workflow != "Pytest" or not re.fullmatch(r"[0-9a-f]{40}", sha) or _git(repo, "rev-parse", "HEAD") != sha:
            return reject("invalid_input", "Expected the Pytest workflow and its exact 40-character commit SHA")
        original_log = log.read_text(errors="replace")
        xml_path = xml if xml is not None and xml.is_file() else None
        report["input_source"] = "pytest.xml" if xml_path is not None else "pytest.log"
        incoming_failures, errors = (
            _xml_failures(xml_path) if xml_path is not None
            else (_failed_tests(original_log), bool(COLLECTION_ERROR.search(original_log)))
        )
        report["incoming_failures"] = sorted(incoming_failures)
        if errors:
            return reject("pytest_error", "Original CI failed during pytest collection or setup")
        if not incoming_failures:
            return reject("not_pytest_failure", "Original CI artifact has no failed pytest node IDs")

        with TemporaryDirectory(prefix="mini-ci-repair-") as temp:
            stage = "checkout"
            roots = {name: Path(temp) / name for name in ("baseline", "agent", "candidate")}
            for root in roots.values():
                subprocess.run(["git", "clone", "--quiet", "--no-local", str(repo), str(root)], check=True)
                _git(root, "checkout", "--detach", sha)

            stage = "baseline"
            baseline = _test(roots["baseline"], image, timeout)
            baseline_output = baseline.stdout + baseline.stderr
            (output / "baseline.log").write_text(baseline_output)
            report.update(
                baseline_failures=sorted(_failed_tests(baseline_output)), baseline_returncode=baseline.returncode
            )
            if _execution_error(baseline):
                return reject("execution_error", "First baseline produced no valid pytest result")
            if not _matches_failures(baseline, incoming_failures):
                return reject("not_reproduced", "First baseline differs from the original pytest failures")

            stage = "baseline_repeat"
            repeat = _test(roots["baseline"], image, timeout)
            repeat_output = repeat.stdout + repeat.stderr
            (output / "baseline_repeat.log").write_text(repeat_output)
            report.update(
                baseline_repeat_failures=sorted(_failed_tests(repeat_output)), baseline_repeat_returncode=repeat.returncode
            )
            if _execution_error(repeat):
                return reject("execution_error", "Repeated baseline produced no valid pytest result")
            if not _matches_failures(repeat, incoming_failures):
                return reject("unstable", "Repeated baseline does not fail on the same pytest nodes")

            stage = "agent_setup"
            config = get_config_from_spec("repository.yaml")
            config["environment"].update(root=roots["agent"], image=image)
            config["agent"].update(
                mode="yolo", confirm_exit=False, wall_time_limit_seconds=timeout,
                output_path=output / "trajectory.json",
            )
            if model:
                config["model"]["model_name"] = model
            environment = get_environment(config["environment"])
            try:
                agent = get_agent(get_model(config=config["model"]), environment, config["agent"])
                stage = "agent"
                try:
                    result = agent.run(
                        "Fix the reproducible pytest failure. Do not modify tests or configuration. "
                        f"Failed workflow: {workflow}; commit: {sha}.\n"
                        f"Failing tests: {', '.join(sorted(incoming_failures))}.\n"
                        f"Original CI log (truncated):\n{original_log[-12000:]}"
                    )
                finally:
                    report.update(model_cost=agent.cost, model_calls=agent.n_calls)
            finally:
                environment.cleanup()
            report["agent_status"] = result.get("exit_status")
            if result.get("exit_status") != "Submitted" or _git(roots["agent"], "rev-parse", "HEAD") != sha:
                return reject("agent_incomplete", "Agent did not submit a patch on the failed commit")

            stage = "changes"
            paths = _allowed_changes(roots["agent"])
            report["changed_files"] = paths
            if not paths:
                return reject("invalid_changes", "Patch must change only tracked src/*.py files")
            patch = _git(roots["agent"], "diff", "HEAD", "--binary", "--no-ext-diff") + "\n"
            (output / "patch.diff").write_text(patch)
            stage = "apply_patch"
            _git(roots["candidate"], "apply", "--check", "-", input=patch)
            _git(roots["candidate"], "apply", "-", input=patch)

            stage = "target"
            target = _test(roots["candidate"], image, timeout, report["baseline_failures"])
            (output / "target.log").write_text(target.stdout + target.stderr)
            if status := _test_status(target, "target_failed"):
                return reject(
                    status,
                    "Original failing tests produced no valid pytest result" if status == "execution_error"
                    else "Original failing tests still fail after the patch",
                )

            stage = "candidate"
            candidate = _test(roots["candidate"], image, timeout)
            (output / "candidate.log").write_text(candidate.stdout + candidate.stderr)
            if status := _test_status(candidate, "candidate_failed"):
                return reject(
                    status,
                    "Full pytest suite produced no valid pytest result" if status == "execution_error"
                    else "Full pytest suite fails after the patch",
                )
            return "verified"

    try:
        report["status"] = repair()
    except subprocess.TimeoutExpired as exc:
        report.update(status="timeout", failed_stage=stage, reason=f"{stage} exceeded {exc.timeout} seconds")
    except Exception as exc:
        report.update(
            status={"input": "invalid_input", "checkout": "checkout_error", "apply_patch": "patch_error"}.get(
                stage, "agent_error" if stage.startswith("agent") else "execution_error"
            ),
            failed_stage=stage,
            error_type=type(exc).__name__,
        )
        raise
    finally:
        report["duration_seconds"] = round(time.monotonic() - started, 2)
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if report["status"] != "verified":
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
