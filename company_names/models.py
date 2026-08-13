"""Domain objects for reviewing cleaned company names."""

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID, uuid4


@dataclass
class Group:
    id: str
    canonical_title: str
    existing: bool


@dataclass
class NameRecord:
    cleaned_name: str
    group_id: str | None
    source: Literal["exact", "suggested", "unknown"]
    selected: bool = False
    excluded: bool = False


@dataclass
class ReviewBoard:
    groups: dict[str, Group]
    names: dict[str, NameRecord]


@dataclass(frozen=True)
class SubmissionPayload:
    groups: list[dict[str, object]]
    mappings: list[dict[str, object]]
    unmap_names: list[str]
    request_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        try:
            canonical_request_id = str(UUID(self.request_id))
        except (AttributeError, TypeError, ValueError):
            raise ValueError("request_id must be a UUID") from None
        object.__setattr__(self, "request_id", canonical_request_id)
