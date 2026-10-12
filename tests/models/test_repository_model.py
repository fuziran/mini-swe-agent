import json

import pytest
from litellm.types.utils import ModelResponse

from minisweagent.config import get_config_from_spec
from minisweagent.environments import get_environment
from minisweagent.environments.repository import RepositoryEnvironment
from minisweagent.exceptions import FormatError
from minisweagent.models import get_model_class
from minisweagent.models.repository_model import RepositoryModel


def response_for(*calls):
    return ModelResponse(
        choices=[
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": f"call_{i}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                        for i, (name, args) in enumerate(calls)
                    ],
                },
            }
        ]
    )


def test_repository_model_parses_multiple_named_tools():
    model = RepositoryModel(model_name="test/model", cost_tracking="ignore_errors")
    assert model._parse_actions(response_for(("search_code", {"query": "answer"}), ("bash", {"command": "pwd"}))) == [
        {"tool_name": "search_code", "arguments": {"query": "answer"}, "tool_call_id": "call_0"},
        {"command": "pwd", "tool_call_id": "call_1"},
    ]
    with pytest.raises(FormatError):
        model._parse_actions(response_for(("read_file", {"start_line": 2})))
    with pytest.raises(FormatError):
        model._parse_actions(response_for(("read_file", {"path": "example.py", "start_line": "2"})))
    with pytest.raises(FormatError):
        model._parse_actions(response_for(("unknown_tool", {})))


def test_repository_config_selects_model_and_environment(tmp_path):
    config = get_config_from_spec("repository.yaml")
    assert get_model_class("test/model", config["model"]["model_class"]) is RepositoryModel
    assert isinstance(
        get_environment(config["environment"] | {"root": str(tmp_path), "runner": "local"}),
        RepositoryEnvironment,
    )
