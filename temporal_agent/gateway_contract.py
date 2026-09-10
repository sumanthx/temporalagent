from __future__ import annotations

from typing import Any


SOURCE_GATEWAY_TOOL_NAMES = (
    "source_changes",
    "source_list_versions",
    "source_get_version",
    "source_get_current",
    "source_search_current",
    "source_check_access",
)


def _schema(
    properties: dict[str, dict[str, Any]],
    required: list[str] | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        value["required"] = required
    return value


SOURCE_GATEWAY_TOOLS = [
    {
        "name": "source_changes",
        "description": "Read source change events after an optional delta cursor.",
        "inputSchema": _schema({
            "cursor": {
                "type": "string",
                "description": "Opaque source delta cursor.",
            },
        }),
    },
    {
        "name": "source_list_versions",
        "description": "List immutable versions for one source document.",
        "inputSchema": _schema({
            "document_id": {
                "type": "string",
                "description": "Stable source document identifier.",
            },
        }, ["document_id"]),
    },
    {
        "name": "source_get_version",
        "description": "Retrieve one exact immutable source document version.",
        "inputSchema": _schema({
            "document_id": {
                "type": "string",
                "description": "Stable source document identifier.",
            },
            "version_id": {
                "type": "string",
                "description": "Source-assigned immutable version identifier.",
            },
        }, ["document_id", "version_id"]),
    },
    {
        "name": "source_get_current",
        "description": "Retrieve the current source document, excluding deletions.",
        "inputSchema": _schema({
            "document_id": {
                "type": "string",
                "description": "Stable source document identifier.",
            },
        }, ["document_id"]),
    },
    {
        "name": "source_search_current",
        "description": (
            "Search current source content after evaluating source permissions "
            "for the trusted principal."
        ),
        "inputSchema": _schema({
            "query": {
                "type": "string",
                "description": "Current-content search terms.",
            },
            "principal_id": {
                "type": "string",
                "description": "Trusted immutable principal identifier.",
            },
            "principal_groups": {
                "type": "array",
                "description": "Trusted immutable group identifiers.",
                "items": {"type": "string"},
            },
        }, ["query", "principal_id"]),
    },
    {
        "name": "source_check_access",
        "description": (
            "Evaluate whether a trusted principal currently has source access."
        ),
        "inputSchema": _schema({
            "document_id": {
                "type": "string",
                "description": "Stable source document identifier.",
            },
            "principal_id": {
                "type": "string",
                "description": "Trusted immutable principal identifier.",
            },
            "principal_groups": {
                "type": "array",
                "description": "Trusted immutable group identifiers.",
                "items": {"type": "string"},
            },
        }, ["document_id", "principal_id"]),
    },
]
