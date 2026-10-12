import subprocess

import pytest

from minisweagent.agents.default import DefaultAgent
from minisweagent.agents.interactive import InteractiveAgent
from minisweagent.environments.repository import RepositoryEnvironment
from minisweagent.exceptions import Submitted
from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output


def test_repository_tools_search_patch_and_test(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "example.py").write_text("def answer():\n    return 41\n")
    (tmp_path / "test_example.py").write_text(
        "from example import answer\n\ndef test_answer():\n    assert answer() == 420\n"
    )
    env = RepositoryEnvironment(root=tmp_path, runner="local")
    assert env.get_template_vars()["execution_root"] == str(tmp_path)
    assert "example.py:2" in env.execute({"tool_name": "search_code", "arguments": {"query": "return 41"}})["output"]
    assert (
        "example.py:1: symbol answer"
        in env.execute({"tool_name": "search_code", "arguments": {"query": "answer"}})["output"]
    )
    assert (
        "FunctionDef answer"
        in env.execute({"tool_name": "list_symbols", "arguments": {"path": "example.py"}})["output"]
    )
    assert "2:     return 41" in env.execute({"tool_name": "read_file", "arguments": {"path": "example.py"}})["output"]
    assert env.execute({"tool_name": "run_tests", "arguments": {"target": "test_example.py"}})["returncode"] != 0
    patch = "--- a/example.py\n+++ b/example.py\n@@ -1,2 +1,2 @@\n def answer():\n-    return 41\n+    return 420\n"
    assert env.execute({"tool_name": "apply_patch", "arguments": {"patch": patch}})["returncode"] == 0
    assert (result := env.execute({"tool_name": "run_tests", "arguments": {"target": "test_example.py"}}))[
        "returncode"
    ] == 0, result
    assert env.execute({"tool_name": "run_tests", "arguments": {}})["returncode"] == 0
    with pytest.raises(Submitted) as submitted:
        env.execute({"tool_name": "finish", "arguments": {"summary": "Fixed and tested"}})
    assert submitted.value.messages[0]["extra"]["submission"] == "Fixed and tested"


def test_repository_tools_reject_paths_outside_root(tmp_path):
    (tmp_path / "test_file.py").write_text("pass\n")
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret")
    (tmp_path / "link.py").symlink_to(outside)
    env = RepositoryEnvironment(root=tmp_path, runner="local")
    for path in ("../outside-secret.txt", "link.py", ".git/config"):
        assert env.execute({"tool_name": "read_file", "arguments": {"path": path}})["returncode"] == -1
    assert "secret" not in env.execute({"tool_name": "search_code", "arguments": {"query": "secret"}})["output"]
    assert env.execute({"tool_name": "run_tests", "arguments": {"target": "../outside-secret.txt"}})["returncode"] == -1
    patch = "--- a/../outside-secret.txt\n+++ b/../outside-secret.txt\n@@ -1 +1 @@\n-secret\n+leaked\n"
    assert env.execute({"tool_name": "apply_patch", "arguments": {"patch": patch}})["returncode"] == -1
    assert outside.read_text() == "secret"


def test_repository_actions_complete_agent_loop(tmp_path):
    (tmp_path / "module.py").write_text("def answer():\n    return 42\n")
    actions = [
        {"tool_name": "search_code", "arguments": {"query": "answer"}, "tool_call_id": "search"},
        {"tool_name": "finish", "arguments": {"summary": "Located answer"}, "tool_call_id": "finish"},
    ]
    model = DeterministicToolcallModel(
        outputs=[
            make_toolcall_output(
                None, [{"id": "search", "type": "function", "function": {"name": "search_code"}}], [actions[0]]
            ),
            make_toolcall_output(
                None, [{"id": "finish", "type": "function", "function": {"name": "finish"}}], [actions[1]]
            ),
        ]
    )
    agent = DefaultAgent(
        model,
        RepositoryEnvironment(root=tmp_path, runner="local"),
        system_template="Repository agent",
        instance_template="{{task}}",
    )
    assert agent.run("Find the answer")["submission"] == "Located answer"
    assert any(message.get("extra", {}).get("tool_name") == "search_code" for message in agent.messages)


def test_interactive_finish_does_not_record_tool_error(tmp_path):
    action = {"tool_name": "finish", "arguments": {"summary": "Done"}, "tool_call_id": "finish"}
    model = DeterministicToolcallModel(
        outputs=[
            make_toolcall_output(None, [{"id": "finish", "type": "function", "function": {"name": "finish"}}], [action])
        ]
    )
    agent = InteractiveAgent(
        model,
        RepositoryEnvironment(root=tmp_path, runner="local"),
        system_template="Repository agent",
        instance_template="{{task}}",
        mode="yolo",
        confirm_exit=False,
    )
    assert agent.run("Finish")["submission"] == "Done"
    assert not any(message.get("extra", {}).get("returncode") == -1 for message in agent.messages)
