import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from minisweagent.run.ci_repair import _allowed_changes

WORKFLOW = Path(__file__).parents[2] / "examples/ci_repair_demo/.github/workflows/repair.yml"
VALIDATE = next(
    step["run"] for step in yaml.safe_load(WORKFLOW.read_text())["jobs"]["publish"]["steps"]
    if step.get("name") == "Validate the verified patch before publishing"
)
EXISTING = next(
    step["run"] for step in yaml.safe_load(WORKFLOW.read_text())["jobs"]["publish"]["steps"]
    if step.get("name") == "Check for an existing repair PR"
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(repo)], check=True)
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / "src/calc.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "tests/test_calc.py").write_text("from src.calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    (repo / ".github/workflows/pytest.yml").write_text("name: Pytest\n")
    (repo / "requirements.txt").write_text("pytest==9.0.2\n")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.filemode", "true")
    _git(repo, "add", "src", "tests", ".github", "requirements.txt")
    _git(repo, "commit", "-qm", "failing pytest")
    _git(repo, "push", "-q", "origin", "HEAD:main")
    return repo, _git(repo, "rev-parse", "HEAD")


def _change(repo: Path, kind: str) -> None:
    source = repo / "src/calc.py"
    if kind in {"source", "untracked"}:
        source.write_text("def add(a, b):\n    return a + b\n")
    if kind == "untracked":
        (repo / "src/extra.py").write_text("answer = 42\n")
    if kind == "tests":
        (repo / "tests/test_calc.py").write_text("def test_add():\n    assert True\n")
    if kind == "workflow":
        (repo / ".github/workflows/pytest.yml").write_text("name: Skipped\n")
    if kind == "deps":
        (repo / "requirements.txt").write_text("pytest==9.1.1\n")
    if kind == "mode":
        source.chmod(source.stat().st_mode | stat.S_IXUSR)
    if kind == "symlink":
        source.unlink()
        source.symlink_to("other.py")


def _patch(repo: Path, sha: str, kind: str = "source") -> None:
    _change(repo, kind)
    _git(repo, "add", "--all")
    patch = _git(repo, "diff", "--cached", "--binary", "HEAD") + "\n"
    changed_files = _git(repo, "diff", "--cached", "--name-only", "HEAD").splitlines()
    _git(repo, "reset", "--hard", "HEAD")
    output = repo / "repair-result"
    output.mkdir()
    (output / "patch.diff").write_text(patch)
    (output / "report.json").write_text(json.dumps({
        "status": "verified", "sha": sha, "workflow": "Pytest", "run_id": 17, "changed_files": changed_files,
    }))


def _validate(repo: Path, sha: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", VALIDATE], cwd=repo,
        env={**os.environ, "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}",
             "FAILED_SHA": sha, "FAILED_RUN_ID": "17"},
        capture_output=True, text=True,
    )


def test_valid_patch_stages_only_verified_source(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    _patch(repo, sha)

    assert _validate(repo, sha).returncode == 0
    assert _git(repo, "diff", "--cached", "--name-only") == "src/calc.py"
    assert "return a + b" in (repo / "src/calc.py").read_text()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "candidate_failed"),
        ("sha", "0" * 40),
        ("workflow", "Other"),
        ("run_id", 18),
        ("changed_files", ["src/other.py"]),
    ],
)
def test_mismatched_report_cannot_stage_patch(tmp_path: Path, field: str, value: object) -> None:
    repo, sha = _repo(tmp_path)
    _patch(repo, sha)
    report = repo / "repair-result/report.json"
    data = json.loads(report.read_text())
    data[field] = value
    report.write_text(json.dumps(data))

    assert _validate(repo, sha).returncode != 0
    assert not _git(repo, "diff", "--cached", "--name-only")


def test_advanced_main_cannot_stage_patch(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    _patch(repo, sha)
    (repo / "README.md").write_text("new main\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "advance main")
    _git(repo, "push", "-q", "origin", "HEAD:main")
    _git(repo, "reset", "--hard", sha)

    assert _validate(repo, sha).returncode != 0
    assert not _git(repo, "diff", "--cached", "--name-only")


def test_invalid_patch_cannot_be_staged(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    _patch(repo, sha)
    (repo / "repair-result/patch.diff").write_text("not a patch\n")

    assert _validate(repo, sha).returncode != 0
    assert not _git(repo, "diff", "--cached", "--name-only")


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("tests", "Patch contains a disallowed path"),
        ("workflow", "Patch contains a disallowed path"),
        ("deps", "Patch contains a disallowed path"),
        ("mode", "Patch changes a disallowed path or file type"),
        ("symlink", "Patch changes a disallowed path or file type"),
        ("untracked", "Patch created untracked files"),
    ],
)
def test_disallowed_patch_cannot_be_staged(tmp_path: Path, kind: str, reason: str) -> None:
    repo, sha = _repo(tmp_path)
    _patch(repo, sha, kind)

    assert reason in _validate(repo, sha).stderr
    assert not _git(repo, "diff", "--cached", "--name-only")


@pytest.mark.parametrize(
    ("kind", "allowed"),
    [("source", ["src/calc.py"]), ("tests", []), ("workflow", []), ("deps", []),
     ("mode", []), ("symlink", []), ("untracked", [])],
)
def test_agent_only_accepts_regular_source_edits(tmp_path: Path, kind: str, allowed: list[str]) -> None:
    repo, _ = _repo(tmp_path)
    _change(repo, kind)

    assert _allowed_changes(repo) == allowed


@pytest.mark.skipif(
    not (os.environ.get("MSWEA_CI_REPAIR_LIVE_REPO") and os.environ.get("MSWEA_CI_REPAIR_LIVE_SHA")),
    reason="Set a repository and failed SHA with an existing repair PR to query GitHub",
)
def test_existing_pr_skips_publication(tmp_path: Path) -> None:
    output = tmp_path / "github-output"
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", EXISTING],
        env={**os.environ, "GH_REPO": os.environ["MSWEA_CI_REPAIR_LIVE_REPO"],
             "FAILED_SHA": os.environ["MSWEA_CI_REPAIR_LIVE_SHA"], "GITHUB_OUTPUT": str(output)},
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text() == "exists=true\n"
