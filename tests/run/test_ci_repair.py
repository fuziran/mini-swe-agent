import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from minisweagent.run.ci_repair import _matches_failures, app


def _git_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calculator.py").write_text("answer = 42\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "-c", "commit.gpgsign=false", "commit", "-qm", "baseline",
        ],
        check=True,
    )
    return repo, subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.mark.parametrize(
    ("returncode", "output", "matches"),
    [
        (1, "FAILED tests/test_calc.py::test_add - assert 1 == 2\n", True),
        (0, "FAILED tests/test_calc.py::test_add - assert 1 == 2\n", False),
        (2, "FAILED tests/test_calc.py::test_add - interrupted\n", False),
        (1, "FAILED tests/test_calc.py::test_subtract - assert 1 == 2\n", False),
        (
            1,
            "FAILED tests/test_calc.py::test_add - assert 1 == 2\n"
            "FAILED tests/test_calc.py::test_subtract - assert 1 == 2\n",
            False,
        ),
    ],
)
def test_matches_only_the_original_pytest_failures(returncode: int, output: str, matches: bool) -> None:
    result = subprocess.CompletedProcess([], returncode, output, "")
    assert _matches_failures(result, {"tests/test_calc.py::test_add"}) is matches


@pytest.mark.parametrize(
    ("workflow", "ci_log", "status"),
    [
        ("Other", "FAILED tests/test_calc.py::test_add - assert 1 == 2\n", "invalid_input"),
        ("Pytest", "ImportError: test collection failed\n", "not_pytest_failure"),
    ],
)
def test_rejects_non_pytest_input_with_report(tmp_path: Path, workflow: str, ci_log: str, status: str) -> None:
    repo, sha = _git_repo(tmp_path)
    log = tmp_path / "pytest.log"
    log.write_text(ci_log)
    output = tmp_path / "output"

    result = CliRunner().invoke(
        app,
        ["--repo", str(repo), "--workflow", workflow, "--sha", sha, "--log", str(log), "--output", str(output)],
    )

    assert result.exit_code == 1
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == status
    assert report["failed_stage"] == "input"
    assert report["duration_seconds"] >= 0
    assert not (output / "patch.diff").exists()


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI is unavailable")
def test_docker_failure_is_not_a_pytest_failure(tmp_path: Path) -> None:
    repo, sha = _git_repo(tmp_path)
    log = tmp_path / "pytest.log"
    log.write_text("FAILED tests/test_calc.py::test_add - assert 1 == 2\n")
    output = tmp_path / "output"

    result = CliRunner().invoke(
        app,
        [
            "--repo", str(repo), "--workflow", "Pytest", "--sha", sha,
            "--log", str(log), "--output", str(output), "--image", "invalid image",
        ],
    )

    assert result.exit_code == 1
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "execution_error"
    assert report["failed_stage"] == "baseline"
    assert report["baseline_returncode"] == 125
    assert (output / "baseline.log").read_text()
    assert not (output / "trajectory.json").exists()


def test_missing_checkout_still_writes_failure_report(tmp_path: Path) -> None:
    log = tmp_path / "pytest.log"
    log.write_text("FAILED tests/test_calc.py::test_add - assert 1 == 2\n")
    output = tmp_path / "output"

    result = CliRunner().invoke(
        app,
        [
            "--repo", str(tmp_path / "missing"), "--workflow", "Pytest", "--sha", "0" * 40,
            "--log", str(log), "--output", str(output),
        ],
    )

    assert result.exit_code != 0
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "invalid_input"
    assert report["failed_stage"] == "input"
    assert report["error_type"] == "FileNotFoundError"
