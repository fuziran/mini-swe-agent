"""Check every frozen case with real Git, Docker, and pytest runs."""

import json
import subprocess
from pathlib import Path

import typer

from minisweagent.run.ci_repair import _xml_failures, _xml_results


def command(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True).stdout.strip()


def pytest_run(
    repo: Path, evidence: Path, name: str, env: dict[str, str], image: str
) -> tuple[int, set[str], bool, dict[str, str]]:
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--cpus",
            "2",
            "--memory",
            "2g",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            *(item for key, value in env.items() for item in ("-e", f"{key}={value}")),
            "--mount",
            f"type=bind,source={repo},target=/workspace,readonly",
            "--mount",
            f"type=bind,source={evidence},target=/results",
            "-w",
            "/workspace",
            image,
            "python",
            "-m",
            "pytest",
            "-q",
            "--color=no",
            "-p",
            "no:cacheprovider",
            f"--junitxml=/results/{name}.xml",
        ],
        text=True,
        capture_output=True,
        timeout=120,
    )
    (evidence / f"{name}.log").write_text(result.stdout + result.stderr)
    failures, errors = _xml_failures(evidence / f"{name}.xml")
    outcomes = {} if errors else _xml_results(evidence / f"{name}.xml", result.returncode)
    return result.returncode, failures, errors, outcomes


def validate(repositories: Path, manifest: Path, evidence: Path, summary: Path) -> None:
    repositories = repositories.resolve()
    evidence = evidence.resolve()
    if evidence.exists():
        raise ValueError(f"Evidence already exists: {evidence}")
    cases = [json.loads(line) for line in manifest.read_text().splitlines()]
    if len(cases) != 20 or len({case["bug_sha"] for case in cases}) != 20:
        raise ValueError("Expected 20 distinct commits")
    image_id = json.loads(command("docker", "image", "inspect", "mini-swe-agent-repository:latest"))[0]["Id"]
    python_version = command("docker", "run", "--rm", "--network", "none", image_id, "python", "--version")
    pytest_version = command(
        "docker", "run", "--rm", "--network", "none", image_id, "python", "-m", "pytest", "--version"
    )
    evidence.mkdir(parents=True)
    results = []
    for case in cases:
        repo = repositories / case["case_id"]
        if command("git", "status", "--porcelain", cwd=repo):
            raise ValueError(f"Generated repository must be clean: {repo}")
        if command("git", "rev-list", "--all", "--count", cwd=repo) != "1":
            raise ValueError(f"Case exposes another commit: {repo}")
        case_evidence = evidence / case["case_id"]
        case_evidence.mkdir()
        original = pytest_run(
            repositories / case["original_log_case"], case_evidence, "original", case["original_env"], image_id
        )
        actual_sha = command("git", "rev-parse", "HEAD", cwd=repo)
        first = pytest_run(repo, case_evidence, "baseline", {}, image_id)
        repeat = pytest_run(repo, case_evidence, "baseline_repeat", {}, image_id)
        expected = set(case["expected_failed_nodes"])
        reproduced = bool(
            not first[2]
            and first[0] == 1
            and first[1] == expected
            and not repeat[2]
            and repeat[0] == 1
            and first[3] == repeat[3]
        )
        valid = bool(
            actual_sha == case["bug_sha"]
            and original[1] == expected
            and (original[2] or original[0] == 1)
            and reproduced == case["expected_reproduced"]
            and (case["case_id"] != "E20" or original[2] and first[2] and repeat[2])
        )
        results.append(
            {
                "case_id": case["case_id"],
                "bug_sha": actual_sha,
                "original_returncode": original[0],
                "original_collection_error": original[2],
                "original_failed_nodes": sorted(original[1]),
                "baseline_returncode": first[0],
                "baseline_collection_error": first[2],
                "baseline_failed_nodes": sorted(first[1]),
                "baseline_repeat_returncode": repeat[0],
                "baseline_repeat_failed_nodes": sorted(repeat[1]),
                "reproduced": reproduced,
                "matches_design": valid,
            }
        )
        typer.echo(f"{case['case_id']}: {'ok' if valid else 'MISMATCH'}")
    summary.write_text(
        json.dumps(
            {"image_id": image_id, "python": python_version, "pytest": pytest_version, "cases": results},
            indent=2,
        )
        + "\n"
    )
    if not all(result["matches_design"] for result in results):
        raise ValueError("One or more cases differ from the frozen design")


if __name__ == "__main__":
    typer.run(validate)
