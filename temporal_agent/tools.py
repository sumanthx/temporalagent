from __future__ import annotations

from difflib import unified_diff

from .models import Principal
from .source import ContentSource
from .store import TemporalGraphStore


class SharePointTools:
    """Allow-listed structured tools. No arbitrary graph query surface exists."""

    NAMES = (
        "search_sharepoint_current", "resolve_entity", "query_temporal_graph",
        "retrieve_version_evidence", "compare_document_versions", "check_access",
    )

    def __init__(self, source: ContentSource, store: TemporalGraphStore):
        self.source, self.store = source, store

    def call(self, name: str, arguments: dict, principal: Principal) -> dict:
        if name not in self.NAMES:
            raise ValueError(f"tool not allowed: {name}")
        return getattr(self, name)(principal=principal, **arguments)

    def search_sharepoint_current(self, query: str, principal: Principal):
        return {"results": [
            {"document_id": v.document_id, "title": v.title, "path": v.path,
             "version_id": v.version_id}
            for v in self.source.search_current(query, principal)
        ]}

    def resolve_entity(self, name: str, principal: Principal):
        self.store.refresh()
        values = sorted({r.entity for r in self.store.records if name.lower() in r.entity.lower()
                         and self.source.check_access(r.document_id, principal)})
        return {"entities": values}

    def query_temporal_graph(self, principal: Principal, entity: str | None = None,
                             relationship: str | None = None, as_of: str | None = None,
                             changed_from: str | None = None, changed_to: str | None = None):
        rows = self.store.query(entity=entity, relationship=relationship, valid_at=as_of,
                                changed_from=changed_from, changed_to=changed_to)
        allowed = [r.evidence() for r in rows if self.source.check_access(r.document_id, principal)]
        return {"evidence": allowed, "access_filtered_count": len(rows) - len(allowed)}

    def retrieve_version_evidence(self, document_id: str, version_id: str,
                                  principal: Principal):
        self._require_access(document_id, principal)
        version = self.source.get_version(document_id, version_id)
        return {"version": {
            "document_id": document_id, "version_id": version_id, "title": version.title,
            "path": version.path, "content": version.content, "valid_from": version.valid_from,
            "modified_at": version.modified_at,
            "citation": f"sharepoint://{document_id}/versions/{version_id}",
            "provenance": version.provenance,
        }}

    def compare_document_versions(self, document_id: str, from_version: str,
                                  to_version: str, principal: Principal):
        self._require_access(document_id, principal)
        before = self.source.get_version(document_id, from_version)
        after = self.source.get_version(document_id, to_version)
        diff = "\n".join(unified_diff(
            before.content.splitlines(), after.content.splitlines(),
            fromfile=from_version, tofile=to_version, lineterm=""))
        return {"document_id": document_id, "from": from_version, "to": to_version,
                "diff": diff, "citations": [
                    f"sharepoint://{document_id}/versions/{from_version}",
                    f"sharepoint://{document_id}/versions/{to_version}",
                ]}

    def check_access(self, document_id: str, principal: Principal):
        return {"document_id": document_id,
                "allowed": self.source.check_access(document_id, principal),
                "policy": "current-access"}

    def _require_access(self, document_id, principal):
        if not self.source.check_access(document_id, principal):
            raise PermissionError("historical evidence denied by current-access policy")

    @staticmethod
    def schemas():
        descriptions = {
            "search_sharepoint_current": ("Search current SharePoint content", {"query": "string"}),
            "resolve_entity": ("Resolve a business entity", {"name": "string"}),
            "query_temporal_graph": ("Query allow-listed temporal facts", {
                "entity": "string?", "relationship": "string?", "as_of": "timestamp?",
                "changed_from": "timestamp?", "changed_to": "timestamp?"}),
            "retrieve_version_evidence": ("Retrieve an exact source version", {
                "document_id": "string", "version_id": "string"}),
            "compare_document_versions": ("Compare exact document versions", {
                "document_id": "string", "from_version": "string", "to_version": "string"}),
            "check_access": ("Check current access", {"document_id": "string"}),
        }
        return [{"name": n, "description": descriptions[n][0],
                 "inputSchema": {"type": "object", "properties": {
                     k: {"type": "string"} for k in descriptions[n][1]}}}
                for n in SharePointTools.NAMES]
