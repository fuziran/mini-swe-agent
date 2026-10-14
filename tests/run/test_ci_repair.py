import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from minisweagent.run.ci_repair import _matches_failures, app


def _git_repo(tmp_path: Path, test: str | None = None) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calculator.py").write_text("answer = 42\n")
    if test is not None:
        (repo / "tests").mkdir()
        (repo / "tests/test_calc.py").write_text(test)
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


def _has_test_image() -> bool:
    return bool(shutil.which("docker")) and subprocess.run(
        ["docker", "image", "inspect", "mini-swe-agent-repository:latest"], capture_output=True
    ).returncode == 0


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
        ("Pytest", "ImportError: test collection failed\n", "pytest_error"),
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


@pytest.mark.parametrize(
    ("test", "pytest_returncode", "status", "failures"),
    [
        ("def test_add():\n    assert 1 == 2\n", 1, "execution_error", ["tests/test_calc.py::test_add"]),
        (
            "import pytest\n\nclass TestCalc:\n    @pytest.mark.parametrize('value', [0])\n"
            "    def test_add(self, value):\n        assert value == 1\n",
            1, "execution_error", ["tests/test_calc.py::TestCalc::test_add[0]"],
        ),
        ("from missing_test_dependency import thing\n", 2, "pytest_error", []),
        ("def test_add():\n    assert 1 == 1\n", 0, "not_pytest_failure", []),
    ],
)
def test_real_pytest_xml_classifies_original_failure(
    tmp_path: Path, test: str, pytest_returncode: int, status: str, failures: list[str]
) -> None:
    repo, sha = _git_repo(tmp_path, test)
    xml = tmp_path / "pytest.xml"
    assert subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}"],
        cwd=repo, capture_output=True, text=True,
    ).returncode == pytest_returncode
    log = tmp_path / "pytest.log"
    log.write_text("FAILED tests/test_calc.py::test_wrong - stale text log\n")
    output = tmp_path / "output"

    result = CliRunner().invoke(
        app,
        [
            "--repo", str(repo), "--workflow", "Pytest", "--sha", sha,
            "--log", str(log), "--xml", str(xml), "--output", str(output), "--image", "invalid image",
        ],
    )

    assert result.exit_code == 1
    report = json.loads((output / "report.json").read_text())
    assert report["input_source"] == "pytest.xml"
    assert report["incoming_failures"] == failures
    assert report["status"] == status
    assert not (output / "trajectory.json").exists()


def test_missing_pytest_xml_uses_text_log(tmp_path: Path) -> None:
    repo, sha = _git_repo(tmp_path)
    log = tmp_path / "pytest.log"
    log.write_text("FAILED tests/test_calc.py::test_add - assert 1 == 2\n")
    output = tmp_path / "output"

    result = CliRunner().invoke(
        app,
        [
            "--repo", str(repo), "--workflow", "Pytest", "--sha", sha,
            "--log", str(log), "--xml", str(tmp_path / "missing.xml"),
            "--output", str(output), "--image", "invalid image",
        ],
    )

    assert result.exit_code == 1
    report = json.loads((output / "report.json").read_text())
    assert report["input_source"] == "pytest.log"
    assert report["incoming_failures"] == ["tests/test_calc.py::test_add"]
    assert report["status"] == "execution_error"


def test_malformed_pytest_xml_fails_closed(tmp_path: Path) -> None:
    repo, sha = _git_repo(tmp_path)
    log = tmp_path / "pytest.log"
    log.write_text("FAILED tests/test_calc.py::test_add - assert 1 == 2\n")
    xml = tmp_path / "pytest.xml"
    xml.write_text("<testsuites>")
    output = tmp_path / "output"

    result = CliRunner().invoke(
        app,
        [
            "--repo", str(repo), "--workflow", "Pytest", "--sha", sha,
            "--log", str(log), "--xml", str(xml), "--output", str(output),
        ],
    )

    assert result.exit_code != 0
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "invalid_input"
    assert report["error_type"] == "ParseError"
    assert not (output / "trajectory.json").exists()


@pytest.mark.skipif(not _has_test_image(), reason="Repository test image is unavailable")
def test_passing_baseline_rejects_stale_failure_log(tmp_path: Path) -> None:
    repo, sha = _git_repo(tmp_path, "def test_add():\n    assert 1 + 1 == 2\n")
    log = tmp_path / "pytest.log"
    log.write_text("FAILED tests/test_calc.py::test_add - assert 1 == 3\n")
    output = tmp_path / "output"

    result = CliRunner().invoke(
        app,
        ["--repo", str(repo), "--workflow", "Pytest", "--sha", sha, "--log", str(log), "--output", str(output)],
    )

    assert result.exit_code == 1
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "not_reproduced"
    assert report["failed_stage"] == "baseline"
    assert report["baseline_returncode"] == 0
    assert report["baseline_failures"] == []
    assert not (output / "trajectory.json").exists()
    assert not (output / "patch.diff").exists()


@pytest.mark.skipif(not _has_test_image(), reason="Repository test image is unavailable")
def test_baseline_timeout_writes_report_without_model_call(tmp_path: Path) -> None:
    repo, sha = _git_repo(tmp_path, "import time\n\ndef test_slow():\n    time.sleep(5)\n    assert False\n")
    log = tmp_path / "pytest.log"
    log.write_text("FAILED tests/test_calc.py::test_slow - assert False\n")
    output = tmp_path / "output"

    result = CliRunner().invoke(
        app,
        [
            "--repo", str(repo), "--workflow", "Pytest", "--sha", sha,
            "--log", str(log), "--output", str(output), "--timeout", "1",
        ],
    )

    assert result.exit_code == 1
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "timeout"
    assert report["failed_stage"] == "baseline"
    assert report["duration_seconds"] >= 1
    assert not (output / "trajectory.json").exists()
    assert not (output / "patch.diff").exists()
