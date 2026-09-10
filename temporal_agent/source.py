from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from typing import Iterable

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

    def apply_permission_override(self, document_id: str, readers: frozenset[str]) -> None:
        versions = self._versions[document_id]
        versions[-1] = replace(versions[-1], readers=readers)


class MicrosoftGraphContentSource(ContentSource):
    """Production seam. Implement with Graph delta, versions and permissions APIs."""

    def __init__(self, tenant_id: str, site_id: str, credential: object):
        self.tenant_id, self.site_id, self.credential = tenant_id, site_id, credential

    def _pending(self):
        raise NotImplementedError("Wire Microsoft Graph SDK behind ContentSource")

    changes = list_versions = get_version = get_current = search_current = check_access = _pending

