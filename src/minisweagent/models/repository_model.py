"""LiteLLM model with explicit tools for repository work."""

import json

from minisweagent.exceptions import FormatError
from minisweagent.models.litellm_model import LitellmModel
from minisweagent.models.utils.actions_toolcall import BASH_TOOL

REPOSITORY_TOOLS = [
    BASH_TOOL,
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Find matching code lines and Python symbols in the repository",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a repository file with line numbers",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_symbols",
            "description": "List Python classes, functions, and imports in a repository file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": "Apply a standard unified diff with --- a/path, +++ b/path and @@ hunks; do not use *** Begin Patch",
            "parameters": {
                "type": "object",
                "properties": {"patch": {"type": "string"}},
                "required": ["patch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run pytest in the repository, optionally for one file or directory",
            "parameters": {
                "type": "object",
                "properties": {"target": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Finish the task with a concise summary of the changes and verification",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]

_SCHEMAS = {tool["function"]["name"]: tool["function"]["parameters"] for tool in REPOSITORY_TOOLS}
_TYPES = {"string": str, "integer": int}


class RepositoryModel(LitellmModel):
    def _query(self, messages: list[dict[str, str]], **kwargs):
        return super()._query(messages, tools=REPOSITORY_TOOLS, **kwargs)

    def _parse_actions(self, response) -> list[dict]:
        calls = response.choices[0].message.tool_calls or []
        if not calls:
            raise FormatError(
                {
                    "role": "user",
                    "content": "Call a repository tool or finish the task.",
                    "extra": {"interrupt_type": "FormatError"},
                }
            )
        actions = []
        for call in calls:
            name = call.function.name
            try:
                arguments = json.loads(call.function.arguments)
            except (json.JSONDecodeError, TypeError) as e:
                raise FormatError(
                    {
                        "role": "user",
                        "content": f"Invalid {name} arguments: {e}",
                        "extra": {"interrupt_type": "FormatError"},
                    }
                ) from e
            schema = _SCHEMAS.get(name, {})
            properties = schema.get("properties", {})
            if (
                name not in _SCHEMAS
                or not isinstance(arguments, dict)
                or not set(schema.get("required", [])) <= arguments.keys()
                or not arguments.keys() <= properties.keys()
                or any(type(value) is not _TYPES[properties[key]["type"]] for key, value in arguments.items())
            ):
                raise FormatError(
                    {
                        "role": "user",
                        "content": f"Invalid arguments for repository tool {name}.",
                        "extra": {"interrupt_type": "FormatError"},
                    }
                )
            actions.append(
                {"command": arguments["command"], "tool_call_id": call.id}
                if name == "bash"
                else {"tool_name": name, "arguments": arguments, "tool_call_id": call.id}
            )
        return actions
