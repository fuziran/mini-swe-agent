import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest
import yaml
from typer.testing import CliRunner

from minisweagent.config import get_config_from_spec
from minisweagent.models.test_models import make_output
from minisweagent.run.ci_repair import (
    _allowed_changes,
    _apply_candidate_patch,
    _candidate_issue,
    _target_passed,
    _test,
    _xml_results,
    app,
)


def _git_repo(tmp_path: Path, test: str | None = None, source: str | None = None) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calculator.py").write_text("answer = 42\n")
    if test is not None:
        (repo / "tests").mkdir()
        (repo / "tests/test_calc.py").write_text(test)
    if source is not None:
        (repo / "src").mkdir()
        (repo / "src/behavior.py").write_text(source)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "baseline",
        ],
        check=True,
    )
    return repo, subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True, capture_output=True, check=True
    ).stdout.strip()


def _has_test_image() -> bool:
    return (
        bool(shutil.which("docker"))
        and subprocess.run(
            ["docker", "image", "inspect", "mini-swe-agent-repository:latest"], capture_output=True
        ).returncode
        == 0
    )


def _behavior(mode: str) -> str:
    return (
        f"import sys\nmode = {mode!r}\n\n"
        "def collect_original():\n"
        "    return mode != 'missing_original' or any('::test_original' in arg for arg in sys.argv)\n"
    )


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
            "--repo",
            str(repo),
            "--workflow",
            "Pytest",
            "--sha",
            sha,
            "--log",
            str(log),
            "--output",
            str(output),
            "--image",
            "invalid image",
        ],
    )

    assert result.exit_code == 1
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "execution_error"
    assert report["failed_stage"] == "baseline"
    assert report["baseline_returncode"] != 0
    assert (output / "baseline.log").read_text()
    assert not (output / "trajectory.json").exists()


def test_missing_checkout_still_writes_failure_report(tmp_path: Path) -> None:
    log = tmp_path / "pytest.log"
    log.write_text("FAILED tests/test_calc.py::test_add - assert 1 == 2\n")
    output = tmp_path / "output"

    result = CliRunner().invoke(
        app,
        [
            "--repo",
            str(tmp_path / "missing"),
            "--workflow",
            "Pytest",
            "--sha",
            "0" * 40,
            "--log",
            str(log),
            "--output",
            str(output),
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
            1,
            "execution_error",
            ["tests/test_calc.py::TestCalc::test_add[0]"],
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
    assert (
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}"],
            cwd=repo,
            capture_output=True,
            text=True,
        ).returncode
        == pytest_returncode
    )
    log = tmp_path / "pytest.log"
    log.write_text("FAILED tests/test_calc.py::test_wrong - stale text log\n")
    output = tmp_path / "output"

    result = CliRunner().invoke(
        app,
        [
            "--repo",
            str(repo),
            "--workflow",
            "Pytest",
            "--sha",
            sha,
            "--log",
            str(log),
            "--xml",
            str(xml),
            "--output",
            str(output),
            "--image",
            "invalid image",
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
            "--repo",
            str(repo),
            "--workflow",
            "Pytest",
            "--sha",
            sha,
            "--log",
            str(log),
            "--xml",
            str(tmp_path / "missing.xml"),
            "--output",
            str(output),
            "--image",
            "invalid image",
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
            "--repo",
            str(repo),
            "--workflow",
            "Pytest",
            "--sha",
            sha,
            "--log",
            str(log),
            "--xml",
            str(xml),
            "--output",
            str(output),
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
    assert ElementTree.parse(output / "baseline.xml").find(".//testcase").attrib["name"] == "test_add"
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
            "--repo",
            str(repo),
            "--workflow",
            "Pytest",
            "--sha",
            sha,
            "--log",
            str(log),
            "--output",
            str(output),
            "--timeout",
            "1",
        ],
    )

    assert result.exit_code == 1
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "timeout"
    assert report["failed_stage"] == "baseline"
    assert report["duration_seconds"] >= 1
    assert not (output / "trajectory.json").exists()
    assert not (output / "patch.diff").exists()


@pytest.mark.skipif(not _has_test_image(), reason="Repository test image is unavailable")
def test_host_credentials_do_not_enter_pytest_container(tmp_path: Path) -> None:
    repo, _ = _git_repo(
        tmp_path,
        "import os\n\ndef test_no_credentials():\n"
        "    assert not {'GITHUB_TOKEN', 'GH_TOKEN', 'DEEPSEEK_API_KEY'} & os.environ.keys()\n",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; from minisweagent.run.ci_repair import _test; "
            "sys.exit(_test(Path(sys.argv[1]), 'mini-swe-agent-repository:latest', 60).returncode)",
            str(repo),
        ],
        env={**os.environ, "GITHUB_TOKEN": "sentinel", "GH_TOKEN": "sentinel", "DEEPSEEK_API_KEY": "sentinel"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not _has_test_image(), reason="Repository test image is unavailable")
def test_real_pytest_xml_records_node_outcomes(tmp_path: Path) -> None:
    repo, _ = _git_repo(
        tmp_path,
        "import pytest\n\n"
        "def test_pass():\n    assert True\n\n"
        "def test_fail():\n    assert False\n\n"
        "@pytest.mark.skip\ndef test_skip():\n    pass\n\n"
        "@pytest.mark.xfail\ndef test_xfail():\n    assert False\n",
    )
    xml = tmp_path / "pytest.xml"
    result = _test(repo, "mini-swe-agent-repository:latest", 60, xml=xml)

    assert result.returncode == 1
    assert _xml_results(xml, result.returncode) == {
        "tests/test_calc.py::test_pass": "passed",
        "tests/test_calc.py::test_fail": "failed",
        "tests/test_calc.py::test_skip": "skipped",
        "tests/test_calc.py::test_xfail": "skipped",
    }
    with pytest.raises(ValueError, match="outcomes do not match"):
        _xml_results(xml, 0)


@pytest.mark.skipif(not _has_test_image(), reason="Repository test image is unavailable")
def test_pytest_xml_is_isolated_and_not_reused(tmp_path: Path) -> None:
    repo, _ = _git_repo(
        tmp_path,
        "from pathlib import Path\nimport pytest\n\n"
        "def test_read_only_source():\n"
        "    with pytest.raises(OSError):\n"
        "        Path('written.txt').write_text('changed')\n",
    )
    xml = tmp_path / "pytest.xml"

    assert _test(repo, "mini-swe-agent-repository:latest", 60, xml=xml).returncode == 0
    assert ElementTree.parse(xml).find(".//testcase").attrib["name"] == "test_read_only_source"
    assert not (repo / "written.txt").exists()
    assert _test(repo, "invalid image", 60, xml=xml).returncode != 0
    assert not xml.exists()


@pytest.mark.skipif(not _has_test_image(), reason="Repository test image is unavailable")
def test_candidate_validation_rejects_skips_and_missing_nodes(tmp_path: Path) -> None:
    repo, _ = _git_repo(
        tmp_path,
        "import pytest\nfrom src.behavior import mode, collect_original\n\n"
        "if collect_original():\n"
        "    def test_original():\n"
        "        if mode == 'skip_original':\n"
        "            pytest.skip('source choice')\n"
        "        assert mode != 'broken'\n\n"
        "def test_other():\n"
        "    if mode == 'skip_other':\n"
        "        pytest.skip('source choice')\n"
        "    assert True\n",
        _behavior("broken"),
    )
    original = "tests/test_calc.py::test_original"
    baseline_xml = tmp_path / "baseline.xml"
    baseline = _test(repo, "mini-swe-agent-repository:latest", 60, xml=baseline_xml)
    assert baseline.returncode == 1
    baseline_results = _xml_results(baseline_xml, baseline.returncode)
    assert baseline_results == {original: "failed", "tests/test_calc.py::test_other": "passed"}

    for mode, expected in [
        ("fixed", "verified"),
        ("skip_original", "target_failed"),
        ("missing_original", "candidate_failed"),
        ("skip_other", "candidate_failed"),
    ]:
        (repo / "src/behavior.py").write_text(_behavior(mode))
        assert _allowed_changes(repo) == ["src/behavior.py"]
        target_xml = tmp_path / "target.xml"
        target = _test(repo, "mini-swe-agent-repository:latest", 60, [original], target_xml)
        target_passed = target.returncode == 0 and _target_passed(
            _xml_results(target_xml, target.returncode), {original}
        )
        if not target_passed:
            assert expected == "target_failed"
            continue

        candidate_xml = tmp_path / "candidate.xml"
        candidate = _test(repo, "mini-swe-agent-repository:latest", 60, xml=candidate_xml)
        assert candidate.returncode == 0
        issue = _candidate_issue(baseline_results, _xml_results(candidate_xml, candidate.returncode), {original})
        assert ("candidate_failed" if issue else "verified") == expected
        if mode == "missing_original":
            assert issue == "Full pytest suite changed collected test nodes"
        if mode == "skip_other":
            assert issue == "Full pytest suite introduced skipped tests"

    invalid_xml = tmp_path / "invalid.xml"
    invalid = _test(repo, "invalid image", 60, xml=invalid_xml)
    assert invalid.returncode not in {0, 1}
    with pytest.raises(FileNotFoundError):
        _xml_results(invalid_xml, invalid.returncode)


@pytest.mark.skipif(not _has_test_image(), reason="Repository test image is unavailable")
def test_candidate_validation_distinguishes_original_failure_from_regression(tmp_path: Path) -> None:
    repo, _ = _git_repo(
        tmp_path,
        "from calculator import answer\n\n"
        "def test_original():\n    assert answer > 50\n\n"
        "def test_regression():\n    assert answer != 100\n",
    )
    source = repo / "calculator.py"
    source.write_text("answer = 43\n")
    xml = tmp_path / "target.xml"
    target = _test(repo, "mini-swe-agent-repository:latest", 60, ["tests/test_calc.py::test_original"], xml)
    assert _xml_results(xml, target.returncode) == {"tests/test_calc.py::test_original": "failed"}

    source.write_text("answer = 100\n")
    target = _test(repo, "mini-swe-agent-repository:latest", 60, ["tests/test_calc.py::test_original"], xml)
    candidate_xml = tmp_path / "candidate.xml"
    candidate = _test(repo, "mini-swe-agent-repository:latest", 60, xml=candidate_xml)
    assert _xml_results(xml, target.returncode) == {"tests/test_calc.py::test_original": "passed"}
    assert _xml_results(candidate_xml, candidate.returncode)["tests/test_calc.py::test_regression"] == "failed"


@pytest.mark.skipif(not _has_test_image(), reason="Repository test image is unavailable")
@pytest.mark.parametrize(
    ("case", "status", "stage", "calls", "cost"),
    [
        ("model_setup_error", "agent_error", "agent_setup", 0, 0.0),
        ("model_error", "agent_error", "agent", 1, 0.0),
        ("invalid_changes", "invalid_changes", "changes", 2, 0.5),
        ("target_failed", "target_failed", "target", 2, 0.5),
        ("candidate_failed", "candidate_failed", "candidate", 2, 0.5),
    ],
)
def test_full_repair_failure_reports(
    tmp_path: Path, case: str, status: str, stage: str, calls: int, cost: float
) -> None:
    test = "from src.behavior import answer\n\ndef test_original():\n    assert answer == 100\n"
    if case == "candidate_failed":
        test += "\ndef test_regression():\n    assert answer != 100\n"
    repo, sha = _git_repo(tmp_path, test, "answer = 42\n")
    config = get_config_from_spec("repository.yaml")
    patch = (
        "--- a/tests/test_calc.py\n+++ b/tests/test_calc.py\n@@ -3,2 +3,2 @@\n def test_original():\n"
        "-    assert answer == 100\n+    assert answer == 42\n"
        if case == "invalid_changes"
        else "--- a/src/behavior.py\n+++ b/src/behavior.py\n@@ -1 +1 @@\n-answer = 42\n"
        f"+answer = {43 if case == 'target_failed' else 100}\n"
    )
    config["model"] = {
        "model_class": "deterministic",
        "model_name": "deterministic",
        "cost_per_call": 0.25,
        "outputs": []
        if case == "model_error"
        else [
            make_output("Apply patch", [{"tool_name": "apply_patch", "arguments": {"patch": patch}}], 0.25),
            make_output("Done", [{"tool_name": "finish", "arguments": {"summary": "Done"}}], 0.25),
        ],
    }
    if case == "model_setup_error":
        config["model"] = {"model_class": "secret-sentinel", "model_name": "deterministic"}
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(yaml.safe_dump(config))
    log = tmp_path / "pytest.log"
    log.write_text("FAILED tests/test_calc.py::test_original - assert 42 == 100\n")
    output = tmp_path / "output"

    result = CliRunner().invoke(
        app,
        [
            "--repo",
            str(repo),
            "--workflow",
            "Pytest",
            "--sha",
            sha,
            "--log",
            str(log),
            "--output",
            str(output),
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code != 0, result.output
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == status
    assert report["failed_stage"] == stage
    assert report["reason"] and "Traceback" not in report["reason"]
    assert report["duration_seconds"] > 0
    assert report["model_calls"] == calls
    assert report["model_cost"] == cost
    assert report["baseline_failures"] == ["tests/test_calc.py::test_original"]
    assert report["baseline_repeat_failures"] == report["baseline_failures"]
    if case == "model_setup_error":
        assert report["error_type"] == "ValueError"
        assert "secret-sentinel" not in report["reason"]
        assert not (output / "trajectory.json").exists()
    elif case == "model_error":
        assert report["error_type"] == "IndexError"
        assert report["reason"] == "Agent or model execution failed"
    else:
        assert json.loads((output / "trajectory.json").read_text())["messages"][3]["extra"]["returncode"] == 0
        if case == "invalid_changes":
            assert report["changed_files"] == []
            assert not (output / "patch.diff").exists()
        elif case == "target_failed":
            assert report["target_failures"] == report["baseline_failures"]
            assert not (output / "candidate.xml").exists()
        else:
            assert report["target_failures"] == []
            assert report["candidate_failures"] == ["tests/test_calc.py::test_regression"]


def test_patch_conflict_reports_safe_reason(tmp_path: Path) -> None:
    repo, _ = _git_repo(tmp_path, source="answer = 42\n")
    (repo / "src/behavior.py").write_text("answer = 50\n")
    report = {"model_calls": 2, "model_cost": 0.5}
    patch = "--- a/src/behavior.py\n+++ b/src/behavior.py\n@@ -1 +1 @@\n-answer = 42\n+answer = 100\n"

    with pytest.raises(subprocess.CalledProcessError):
        _apply_candidate_patch(repo, patch, report)

    assert report == {
        "model_calls": 2,
        "model_cost": 0.5,
        "status": "patch_error",
        "failed_stage": "apply_patch",
        "reason": "Could not apply the patch to the candidate checkout",
        "error_type": "CalledProcessError",
    }
    assert (repo / "src/behavior.py").read_text() == "answer = 50\n"
