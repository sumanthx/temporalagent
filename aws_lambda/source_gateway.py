from __future__ import annotations

import json
import os
from typing import Any

from temporal_agent.models import Principal
from temporal_agent.source import (
    ContentSource,
    mock_source_from_payload,
    source_version_to_dict,
)


def _principal(arguments: dict[str, Any]) -> Principal:
    return Principal(
        arguments["principal_id"],
        frozenset(arguments.get("principal_groups", [])),
    )


def dispatch(
    source: ContentSource,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if tool_name == "source_changes":
        events, cursor = source.changes(arguments.get("cursor"))
        return {"events": events, "cursor": cursor}
    if tool_name == "source_list_versions":
        return {
            "versions": [
                source_version_to_dict(version)
                for version in source.list_versions(arguments["document_id"])
            ]
        }
    if tool_name == "source_get_version":
        return {
            "version": source_version_to_dict(source.get_version(
                arguments["document_id"],
                arguments["version_id"],
            ))
        }
    if tool_name == "source_get_current":
        version = source.get_current(arguments["document_id"])
        return {
            "version": source_version_to_dict(version) if version else None
        }
    if tool_name == "source_search_current":
        return {
            "versions": [
                source_version_to_dict(version)
                for version in source.search_current(
                    arguments["query"],
                    _principal(arguments),
                )
            ]
        }
    if tool_name == "source_check_access":
        return {
            "allowed": source.check_access(
                arguments["document_id"],
                _principal(arguments),
            )
        }
    raise ValueError(f"unknown ContentSource Gateway tool: {tool_name}")


def _load_source() -> ContentSource:
    import boto3

    s3 = boto3.client("s3")
    bucket = os.environ["ARTIFACT_BUCKET"]
    repository = json.loads(
        s3.get_object(
            Bucket=bucket,
            Key="mock-source/repository.json",
        )["Body"].read()
    )
    changes = [
        json.loads(line)
        for line in s3.get_object(
            Bucket=bucket,
            Key="mock-source/changes.jsonl",
        )["Body"].read().decode().splitlines()
        if line.strip()
    ]
    return mock_source_from_payload(repository, changes)


def handle(
    event: dict[str, Any],
    context: Any,
) -> dict[str, Any]:
    custom = getattr(
        getattr(context, "client_context", None),
        "custom",
        {},
    ) or {}
    full_name = custom.get("bedrockAgentCoreToolName", "")
    tool_name = full_name.rsplit("___", 1)[-1]
    if not tool_name:
        raise ValueError("Gateway invocation did not include a tool name")
    return dispatch(_load_source(), tool_name, event)
