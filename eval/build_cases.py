"""Materialize the frozen evaluation cases as independent Git commits."""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import typer


@dataclass(frozen=True)
class Case:
    case_id: str
    category: str
    description: str
    path: str
    before: str | None
    after: str
    failed_tests: tuple[str, ...]
    expected_reproduced: bool = True
    eligible_fix: bool = True
    original_log_case: str | None = None
    original_env: dict[str, str] | None = None


TEST = "tests/test_behavior.py::"
CASES = [
    Case(
        "E01",
        "simple_assertion",
        "Slug case conversion",
        "src/core.py",
        "word.lower()",
        "word",
        (TEST + "test_slugify",),
    ),
    Case(
        "E02",
        "simple_assertion",
        "Zero quantity accepted",
        "src/core.py",
        "quantity <= 0",
        "quantity < 0",
        (TEST + "test_parse_quantity",),
    ),
    Case(
        "E03",
        "simple_assertion",
        "Unicode case folding lost",
        "src/core.py",
        ".casefold()",
        ".lower()",
        (TEST + "test_normalize_name",),
    ),
    Case(
        "E04",
        "exception",
        "Invalid port silently accepted",
        "src/core.py",
        'raise ValueError("port out of range")',
        "return 0",
        (TEST + "test_parse_port",),
    ),
    Case(
        "E05",
        "exception",
        "Missing user no longer raises",
        "src/accounts.py",
        "raise KeyError(user_id)",
        "return {}",
        (TEST + "test_require_user",),
    ),
    Case(
        "E06",
        "exception",
        "Zero denominator silently accepted",
        "src/core.py",
        'raise ZeroDivisionError("zero denominator")',
        "return 0.0",
        (TEST + "test_safe_ratio",),
    ),
    Case(
        "E07",
        "boundary",
        "Partial final page dropped",
        "src/core.py",
        "(total + size - 1) // size",
        "total // size",
        (TEST + "test_page_count",),
    ),
    Case(
        "E08",
        "boundary",
        "Century leap-year rule lost",
        "src/core.py",
        "year % 400 == 0 or (year % 4 == 0 and year % 100 != 0)",
        "year % 4 == 0",
        (TEST + "test_is_leap_year",),
    ),
    Case(
        "E09",
        "boundary",
        "Touching intervals counted as overlapping",
        "src/core.py",
        "left_start < right_end and right_start < left_end",
        "left_start <= right_end and right_start <= left_end",
        (TEST + "test_overlaps",),
    ),
    Case(
        "E10",
        "multi_file",
        "Report bypasses shared name normalizer",
        "src/report.py",
        'return normalize_name(user["name"]).title()',
        'return user["name"].title()',
        (TEST + "test_display_name",),
    ),
    Case(
        "E11",
        "multi_file",
        "Invoice drops line quantities",
        "src/invoice.py",
        'sum((round_cents(price * quantity) for price, quantity in lines), Decimal("0.00"))',
        'round_cents(sum((price for price, quantity in lines), Decimal("0.00")))',
        (TEST + "test_invoice_total",),
    ),
    Case(
        "E12",
        "multi_file",
        "Report bypasses account lookup contract",
        "src/report.py",
        'role in require_user(users, user_id)["roles"]',
        'role in users.get(user_id, {}).get("roles", [])',
        (TEST + "test_can_read",),
    ),
    Case(
        "E13",
        "regression_prone",
        "Clamp loses lower bound",
        "src/core.py",
        "max(low, min(high, value))",
        "min(high, value)",
        (TEST + "test_clamp",),
    ),
    Case(
        "E14",
        "regression_prone",
        "Deduplication changes order",
        "src/collections.py",
        "list(dict.fromkeys(items))",
        "sorted(set(items))",
        (TEST + "test_stable_unique",),
    ),
    Case(
        "E15",
        "regression_prone",
        "Half-up rounding replaced with ties-even",
        "src/core.py",
        "rounding=ROUND_HALF_UP",
        'rounding="ROUND_HALF_EVEN"',
        (TEST + "test_round_cents",),
    ),
    Case(
        "E16",
        "regression_prone",
        "Path prefix check admits sibling directory",
        "src/security.py",
        "candidate.is_relative_to(root)",
        "str(candidate).startswith(str(root))",
        (TEST + "test_safe_child",),
    ),
    Case(
        "E17",
        "not_reproduced",
        "Real failure log paired with a healthy SHA",
        "src/evaluation_marker.py",
        None,
        "REVISION = 17\n",
        (TEST + "test_slugify",),
        False,
        False,
        "E01",
    ),
    Case(
        "E18",
        "not_reproduced",
        "CI-only environment flag is absent in replay",
        "tests/test_environment.py",
        None,
        'import os\n\n\ndef test_environment_flag() -> None:\n    assert os.environ.get("EVAL_CI_FAULT") != "1"\n',
        ("tests/test_environment.py::test_environment_flag",),
        False,
        False,
        None,
        {"EVAL_CI_FAULT": "1"},
    ),
    Case(
        "E19",
        "out_of_scope",
        "Incorrect literal assertion lives in tests",
        "tests/test_incorrect_expectation.py",
        None,
        "def test_incorrect_expectation() -> None:\n    assert 2 + 2 == 5\n",
        ("tests/test_incorrect_expectation.py::test_incorrect_expectation",),
        True,
        False,
    ),
    Case(
        "E20",
        "out_of_scope",
        "Missing test dependency fails collection",
        "tests/test_missing_dependency.py",
        None,
        "import evaluation_package_that_does_not_exist\n",
        (),
        False,
        False,
    ),
]


def git(root: Path, env: dict[str, str], *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, env=env, text=True, capture_output=True, check=True).stdout.strip()


def build(output: Path, manifest: Path) -> None:
    if output.exists():
        raise ValueError(f"Output already exists: {output}")
    if len(CASES) != 20 or len({case.case_id for case in CASES}) != 20:
        raise ValueError("Expected 20 distinct cases")
    output.mkdir()
    fixture = Path(__file__).parent / "fixture"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "CI Repair Evaluation",
        "GIT_AUTHOR_EMAIL": "eval@example.invalid",
        "GIT_COMMITTER_NAME": "CI Repair Evaluation",
        "GIT_COMMITTER_EMAIL": "eval@example.invalid",
        "GIT_AUTHOR_DATE": "2026-10-14T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-10-14T00:00:00+00:00",
    }
    healthy = output / "healthy"
    shutil.copytree(fixture, healthy, ignore=ignore)
    git(healthy, env, "init", "-q", "--initial-branch=main")
    git(healthy, env, "add", "-A")
    git(healthy, env, "commit", "-q", "-m", "eval: healthy fixture")
    base_sha = git(healthy, env, "rev-parse", "HEAD")
    records = []
    for case in CASES:
        root = output / case.case_id
        shutil.copytree(fixture, root, ignore=ignore)
        path = root / case.path
        if case.before is None:
            if path.exists():
                raise ValueError(f"Fixture path already exists: {path}")
            path.write_text(case.after)
        else:
            source = path.read_text()
            if source.count(case.before) != 1:
                raise ValueError(f"Expected one matching mutation in {case.path}: {case.before}")
            path.write_text(source.replace(case.before, case.after, 1))
        git(root, env, "init", "-q", "--initial-branch=main")
        git(root, env, "add", "-A")
        git(root, env, "commit", "-q", "-m", f"eval: {case.case_id} {case.description}")
        records.append(
            {
                "case_id": case.case_id,
                "base_sha": base_sha,
                "bug_sha": git(root, env, "rev-parse", "HEAD"),
                "environment_id": "eval-v1",
                "category": case.category,
                "description": case.description,
                "expected_failed_nodes": list(case.failed_tests),
                "expected_reproduced": case.expected_reproduced,
                "eligible_fix": case.eligible_fix,
                "original_log_case": case.original_log_case or case.case_id,
                "original_env": case.original_env or {},
            }
        )
    manifest.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records))


if __name__ == "__main__":
    typer.run(build)
