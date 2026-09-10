from __future__ import annotations

import hashlib

from .models import MAX_TIME, SourceVersion, TemporalRecord


class TemporalGraphStore:
    """Append-oriented in-memory test substitute for the DynamoDB fact store."""

    def __init__(self):
        self.records: list[TemporalRecord] = []
        self.ingested_events: set[str] = set()

    def refresh(self) -> None:
        """Refresh an external projection; in-memory test data is already current."""

    def ingest(self, version: SourceVersion, recorded_at: str, event_id: str) -> None:
        if event_id in self.ingested_events:
            return
        self.ingested_events.add(event_id)
        for claim in version.claims:
            self._insert_claim(version, claim, recorded_at)
        if version.deleted:
            self._insert_claim(
                version,
                {"entity": version.document_id, "relationship": "status", "value": "deleted"},
                recorded_at,
                tombstone=True,
            )

    def _insert_claim(self, version, claim, recorded_at, tombstone=False):
        entity, relationship = claim["entity"], claim["relationship"]
        valid_from = claim.get("valid_from", version.valid_from)
        competing = [
            r for r in self.records
            if r.document_id == version.document_id
            and r.entity == entity and r.relationship == relationship
            and not r.tombstone and r.recorded_to == MAX_TIME
        ]
        later_starts = [r.valid_from for r in competing if r.valid_from > valid_from]
        valid_to = min(later_starts, default=MAX_TIME)
        for old in competing:
            if old.valid_from < valid_from < old.valid_to:
                old.valid_to = valid_from
            elif old.valid_from == valid_from and old.value != str(claim["value"]):
                # Conflicting simultaneous claims remain visible as alternatives.
                pass
        raw = f"{version.document_id}:{version.version_id}:{entity}:{relationship}:{claim['value']}"
        self.records.append(TemporalRecord(
            record_id=hashlib.sha256(raw.encode()).hexdigest()[:16],
            document_id=version.document_id,
            version_id=version.version_id,
            entity=entity,
            relationship=relationship,
            value=str(claim["value"]),
            valid_from=valid_from,
            valid_to=valid_to,
            recorded_from=recorded_at,
            recorded_to=MAX_TIME,
            source_path=version.path,
            citation=f"sharepoint://{version.document_id}/versions/{version.version_id}",
            provenance={**version.provenance, "modified_at": version.modified_at},
            tombstone=tombstone,
        ))
        self._rebuild_intervals(entity, relationship, version.document_id)

    def _rebuild_intervals(self, entity: str, relationship: str, document_id: str):
        rows = sorted(
            (r for r in self.records if r.entity == entity and r.relationship == relationship
             and r.document_id == document_id and not r.tombstone),
            key=lambda r: r.valid_from,
        )
        starts = sorted(set(r.valid_from for r in rows))
        for row in rows:
            later = [start for start in starts if start > row.valid_from]
            row.valid_to = min(later, default=MAX_TIME)

    def query(self, *, entity=None, relationship=None, valid_at=None,
              changed_from=None, changed_to=None) -> list[TemporalRecord]:
        self.refresh()
        rows = self.records
        if entity:
            rows = [r for r in rows if r.entity.lower() == entity.lower()]
        if relationship:
            rows = [r for r in rows if r.relationship == relationship]
        if valid_at:
            rows = [r for r in rows if r.valid_from <= valid_at < r.valid_to]
        if changed_from:
            rows = [r for r in rows if r.valid_from >= changed_from]
        if changed_to:
            rows = [r for r in rows if r.valid_from < changed_to]
        return sorted(rows, key=lambda r: (r.valid_from, r.recorded_from, r.record_id))
