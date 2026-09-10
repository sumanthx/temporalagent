from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


MAX_TIME = "9999-12-31T23:59:59Z"


@dataclass(frozen=True)
class Principal:
    id: str
    groups: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SourceVersion:
    document_id: str
    version_id: str
    title: str
    path: str
    modified_at: str
    valid_from: str
    content: str
    claims: tuple[dict[str, Any], ...]
    readers: frozenset[str]
    deleted: bool = False
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class TemporalRecord:
    record_id: str
    document_id: str
    version_id: str
    entity: str
    relationship: str
    value: str
    valid_from: str
    valid_to: str
    recorded_from: str
    recorded_to: str
    source_path: str
    citation: str
    provenance: dict[str, Any]
    tombstone: bool = False

    def visible_at(self, valid_at: str, recorded_at: str = MAX_TIME) -> bool:
        return (
            self.valid_from <= valid_at < self.valid_to
            and self.recorded_from <= recorded_at < self.recorded_to
        )

    def evidence(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "relationship": self.relationship,
            "value": self.value,
            "validity": {"from": self.valid_from, "to": self.valid_to},
            "recorded": {"from": self.recorded_from, "to": self.recorded_to},
            "source": {
                "artifact": self.source_path,
                "document_id": self.document_id,
                "version_id": self.version_id,
            },
            "citation": self.citation,
            "provenance": self.provenance,
            "tombstone": self.tombstone,
        }
