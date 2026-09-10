from __future__ import annotations

from .clock import LogicalClock
from .source import ContentSource
from .store import TemporalGraphStore


class ChangeIngester:
    """Local replay worker; production equivalents are Graph webhook -> SQS -> worker."""

    def __init__(self, source: ContentSource, store: TemporalGraphStore, clock: LogicalClock):
        self.source, self.store, self.clock = source, store, clock
        self.cursor: str | None = None

    def replay(self) -> str:
        events, next_cursor = self.source.changes(self.cursor)
        for event in events:
            self.clock.set(event["recorded_at"])
            version = self.source.get_version(event["document_id"], event["version_id"])
            self.store.ingest(version, self.clock.now, event["event_id"])
        self.cursor = next_cursor
        return next_cursor

