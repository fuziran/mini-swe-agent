"""Repository tools backed by a bounded workspace and an execution environment."""

import ast
import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from minisweagent.environments.docker import DockerEnvironment
from minisweagent.environments.local import LocalEnvironment
from minisweagent.exceptions import Submitted


@dataclass
class RepositoryEnvironmentConfig:
    root: Path = Path(".")
    runner: Literal["docker", "local"] = "docker"
    image: str = "mini-swe-agent-repository:latest"
    timeout: int = 60
    max_file_bytes: int = 1_000_000
    max_search_results: int = 40


class RepositoryEnvironment:
    def __init__(self, **kwargs):
        self.config = RepositoryEnvironmentConfig(**kwargs)
        if self.config.runner not in {"docker", "local"}:
            raise ValueError(f"Unknown runner: {self.config.runner}")
        if self.config.max_file_bytes <= 0 or self.config.max_search_results <= 0:
            raise ValueError("Repository search limits must be positive")
        self.root = Path(self.config.root).resolve()
        if not self.root.is_dir():
            raise NotADirectoryError(self.root)
        self.runner = (
            DockerEnvironment(
                image=self.config.image,
                cwd="/workspace",
                timeout=self.config.timeout,
                run_args=[
                    "--rm",
                    "--network",
                    "none",
                    "--cpus",
                    "2",
                    "--memory",
                    "2g",
                    "--pids-limit",
                    "256",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    *(["--user", f"{os.getuid()}:{os.getgid()}"] if os.name == "posix" else []),
                    "--read-only",
                    "--tmpfs",
                    "/tmp",
                    "-e",
                    "HOME=/tmp",
                    "-e",
                    "PYTHONDONTWRITEBYTECODE=1",
                    "-e",
                    "PYTEST_ADDOPTS=-p no:cacheprovider",
                    "--mount",
                    f"type=bind,source={self.root},target=/workspace,readonly",
                ],
            )
            if self.config.runner == "docker"
            else LocalEnvironment(cwd=str(self.root), timeout=self.config.timeout)
        )

    def _path(self, path: str) -> Path:
        candidate = (self.root / path).resolve()
        if not candidate.is_relative_to(self.root) or ".git" in candidate.relative_to(self.root).parts:
            raise ValueError(f"Path is outside the repository: {path}")
        return candidate

    def _text(self, path: Path) -> str:
        if path.stat().st_size > self.config.max_file_bytes:
            raise ValueError(f"File is too large: {path.relative_to(self.root)}")
        return path.read_text(encoding="utf-8", errors="replace")

    def _files(self):
        pending = [self.root]
        while pending:
            for path in pending.pop().iterdir():
                if path.name.startswith(".") or path.name in {"node_modules", "__pycache__", "venv"}:
                    continue
                if not path.resolve().is_relative_to(self.root):
                    continue
                if path.is_dir() and not path.is_symlink():
                    pending.append(path)
                elif path.is_file():
                    yield path

    def _search_code(self, query: str) -> str:
        if not query.strip():
            raise ValueError("Search query must not be empty")
        matches: list[tuple[int, str]] = []
        needle = query.casefold()
        for path in self._files():
            if path.stat().st_size > self.config.max_file_bytes:
                continue
            relative = path.relative_to(self.root)
            if needle in str(relative).casefold():
                matches.append((3, f"{relative}: filename match"))
            content = path.read_bytes()
            if b"\0" in content:
                continue
            source = content.decode("utf-8", errors="replace")
            if path.suffix == ".py":
                try:
                    tree = ast.parse(source, filename=str(relative))
                except SyntaxError:
                    tree = None
                if tree is not None:
                    for node in ast.walk(tree):
                        if (
                            isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                            and needle in node.name.casefold()
                        ):
                            matches.append(
                                (
                                    5 if needle == node.name.casefold() else 4,
                                    f"{relative}:{node.lineno}: symbol {node.name}",
                                )
                            )
            for line_number, line in enumerate(source.splitlines(), 1):
                if needle in line.casefold():
                    matches.append((1, f"{relative}:{line_number}: {line[:200]}"))
                if len(matches) >= self.config.max_search_results * 10:
                    break
        return (
            "\n".join(
                hit
                for _, hit in sorted(matches, key=lambda item: (-item[0], item[1]))[: self.config.max_search_results]
            )
            or "No matches"
        )

    def _read_file(self, path: str, start_line: int = 1, end_line: int = 200) -> str:
        lines = self._text(self._path(path)).splitlines()
        if start_line < 1 or end_line < start_line or end_line - start_line > 300:
            raise ValueError("Invalid line range (at most 301 lines)")
        return "\n".join(f"{i}: {lines[i - 1]}" for i in range(start_line, min(end_line, len(lines)) + 1))

    def _list_symbols(self, path: str) -> str:
        source = self._path(path)
        if source.suffix != ".py":
            raise ValueError("Symbol listing currently supports Python files")
        tree = ast.parse(self._text(source), filename=str(source))
        symbols = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(f"{node.lineno}: {type(node).__name__} {node.name}")
            elif isinstance(node, ast.Import):
                symbols.append(f"{node.lineno}: import {', '.join(alias.name for alias in node.names)}")
            elif isinstance(node, ast.ImportFrom):
                symbols.append(
                    f"{node.lineno}: from {node.module} import {', '.join(alias.name for alias in node.names)}"
                )
        return "\n".join(sorted(symbols, key=lambda item: int(item.split(":", 1)[0]))) or "No symbols"

    def _apply_patch(self, patch: str) -> tuple[str, int]:
        paths = [line[6:].strip() for line in patch.splitlines() if line.startswith(("--- a/", "+++ b/"))]
        if not paths:
            raise ValueError("Patch has no file paths")
        for path in paths:
            self._path(path)
        for args in (["git", "apply", "--check", "-"], ["git", "apply", "-"]):
            result = subprocess.run(
                args, cwd=self.root, input=patch, text=True, capture_output=True, timeout=self.config.timeout
            )
            if result.returncode:
                return result.stderr or result.stdout, result.returncode
        return f"Applied patch to {len(set(paths))} file(s)", 0

    def execute(self, action: dict, cwd: str = "") -> dict[str, Any]:
        name = action.get("tool_name", "bash")
        args = action.get("arguments", {})
        start = time.perf_counter()
        if name == "finish":
            summary = args["summary"]
            raise Submitted(
                {"role": "exit", "content": summary, "extra": {"exit_status": "Submitted", "submission": summary}}
            )
        try:
            if name == "bash":
                output = self.runner.execute(action)
            elif name == "run_tests":
                target = args.get("target", "")
                if target and not self._path(target).exists():
                    raise FileNotFoundError(target)
                executable = shlex.quote(sys.executable) if self.config.runner == "local" else "python"
                command = f"{executable} -m pytest -q"
                output = self.runner.execute({"command": f"{command} {shlex.quote(target)}" if target else command})
            elif name == "apply_patch":
                result, returncode = self._apply_patch(args["patch"])
                output = {"output": result, "returncode": returncode, "exception_info": ""}
            else:
                if name not in {"search_code", "read_file", "list_symbols"}:
                    raise ValueError(f"Unknown repository tool: {name}")
                result = {
                    "search_code": lambda: self._search_code(args["query"]),
                    "read_file": lambda: self._read_file(
                        args["path"], args.get("start_line", 1), args.get("end_line", 200)
                    ),
                    "list_symbols": lambda: self._list_symbols(args["path"]),
                }[name]()
                output = {"output": result, "returncode": 0, "exception_info": ""}
        except (OSError, ValueError, SyntaxError, subprocess.TimeoutExpired) as e:
            output = {"output": "", "returncode": -1, "exception_info": str(e)}
        output.setdefault("extra", {})["duration_seconds"] = time.perf_counter() - start
        output["extra"]["tool_name"] = name
        return output

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return {
            "repository_root": str(self.root),
            "execution_root": "/workspace" if self.config.runner == "docker" else str(self.root),
            **kwargs,
        }

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "environment": asdict(self.config) | {"root": str(self.root)},
                    "environment_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                }
            }
        }

    def cleanup(self) -> None:
        if isinstance(self.runner, DockerEnvironment):
            self.runner.cleanup()
