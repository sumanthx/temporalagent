from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable

from .models import Principal, SourceVersion


class ContentSource(ABC):
    """Swappable boundary for Graph/SharePoint change and content access."""

    @abstractmethod
    def changes(self, cursor: str | None = None) -> tuple[list[dict], str]: ...

    @abstractmethod
    def list_versions(self, document_id: str) -> list[SourceVersion]: ...

    @abstractmethod
    def get_version(self, document_id: str, version_id: str) -> SourceVersion: ...

    @abstractmethod
    def get_current(self, document_id: str) -> SourceVersion | None: ...

    @abstractmethod
    def search_current(self, query: str, principal: Principal) -> list[SourceVersion]: ...

    @abstractmethod
    def check_access(self, document_id: str, principal: Principal) -> bool: ...


class MockContentSource(ContentSource):
    def __init__(self, versions: Iterable[SourceVersion], events: list[dict]):
        self._versions: dict[str, list[SourceVersion]] = {}
        for version in versions:
            self._versions.setdefault(version.document_id, []).append(version)
        for rows in self._versions.values():
            rows.sort(key=lambda v: (v.valid_from, v.version_id))
        self._events = events

    def changes(self, cursor: str | None = None) -> tuple[list[dict], str]:
        offset = int(cursor or 0)
        return self._events[offset:], str(len(self._events))

    def list_versions(self, document_id: str) -> list[SourceVersion]:
        return list(self._versions.get(document_id, []))

    def get_version(self, document_id: str, version_id: str) -> SourceVersion:
        for version in self._versions.get(document_id, []):
            if version.version_id == version_id:
                return version
        raise KeyError(f"unknown version {document_id}/{version_id}")

    def get_current(self, document_id: str) -> SourceVersion | None:
        versions = self._versions.get(document_id, [])
        if not versions:
            return None
        current = max(versions, key=lambda v: (v.valid_from, v.version_id))
        return None if current.deleted else current

    def search_current(self, query: str, principal: Principal) -> list[SourceVersion]:
        words = query.lower().split()
        results = []
        for document_id in self._versions:
            current = self.get_current(document_id)
            if current and self.check_access(document_id, principal):
                haystack = f"{current.title} {current.path} {current.content}".lower()
                if all(word in haystack for word in words):
                    results.append(current)
        return results

    def check_access(self, document_id: str, principal: Principal) -> bool:
        current = self.get_current(document_id)
        if not current:
            return False
        subjects = {principal.id, *principal.groups}
        return bool(subjects & current.readers)


def source_version_from_dict(value: dict[str, Any]) -> SourceVersion:
    return SourceVersion(
        document_id=value["document_id"],
        version_id=value["version_id"],
        title=value["title"],
        path=value["path"],
        modified_at=value["modified_at"],
        valid_from=value["valid_from"],
        content=value["content"],
        claims=tuple(value.get("claims", [])),
        readers=frozenset(value.get("readers", [])),
        deleted=bool(value.get("deleted", False)),
        provenance=value.get("provenance", {}),
    )


def source_version_to_dict(value: SourceVersion) -> dict[str, Any]:
    return {
        "document_id": value.document_id,
        "version_id": value.version_id,
        "title": value.title,
        "path": value.path,
        "modified_at": value.modified_at,
        "valid_from": value.valid_from,
        "content": value.content,
        "claims": list(value.claims),
        "readers": sorted(value.readers),
        "deleted": value.deleted,
        "provenance": value.provenance,
    }


def mock_source_from_payload(
    repository: dict[str, Any],
    events: list[dict],
) -> MockContentSource:
    return MockContentSource(
        [source_version_from_dict(value) for value in repository["versions"]],
        events,
    )


class GatewayContentSource(ContentSource):
    """Stable source boundary backed by an AgentCore Gateway target."""

    def __init__(self, client: Any):
        self.client = client

    def changes(self, cursor: str | None = None) -> tuple[list[dict], str]:
        result = self.client.call(
            "source_changes",
            **({"cursor": cursor} if cursor is not None else {}),
        )
        return result["events"], result["cursor"]

    def list_versions(self, document_id: str) -> list[SourceVersion]:
        result = self.client.call(
            "source_list_versions", document_id=document_id)
        return [
            source_version_from_dict(value)
            for value in result["versions"]
        ]

    def get_version(
        self,
        document_id: str,
        version_id: str,
    ) -> SourceVersion:
        result = self.client.call(
            "source_get_version",
            document_id=document_id,
            version_id=version_id,
        )
        return source_version_from_dict(result["version"])

    def get_current(self, document_id: str) -> SourceVersion | None:
        result = self.client.call(
            "source_get_current", document_id=document_id)
        value = result.get("version")
        return source_version_from_dict(value) if value else None

    def search_current(
        self,
        query: str,
        principal: Principal,
    ) -> list[SourceVersion]:
        result = self.client.call(
            "source_search_current",
            query=query,
            principal_id=principal.id,
            principal_groups=sorted(principal.groups),
        )
        return [
            source_version_from_dict(value)
            for value in result["versions"]
        ]

    def check_access(
        self,
        document_id: str,
        principal: Principal,
    ) -> bool:
        result = self.client.call(
            "source_check_access",
            document_id=document_id,
            principal_id=principal.id,
            principal_groups=sorted(principal.groups),
        )
        return bool(result["allowed"])


class MicrosoftGraphContentSource(ContentSource):
    """Gateway-target implementation seam for Microsoft Graph."""

    def __init__(self, tenant_id: str, site_id: str, credential: object):
        self.tenant_id, self.site_id, self.credential = tenant_id, site_id, credential

    def _pending(self, *_args, **_kwargs):
        raise NotImplementedError(
            "Implement this contract in the Gateway source target")

    changes = list_versions = get_version = get_current = search_current = check_access = _pending
