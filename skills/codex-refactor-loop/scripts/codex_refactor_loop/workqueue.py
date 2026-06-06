"""Key-only work queue primitives for controller tick scheduling."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from typing import Mapping


FORBIDDEN_PAYLOAD_FIELDS = frozenset(
    {
        "argv",
        "cmd",
        "command",
        "command_line",
        "commands",
        "env",
        "executor",
        "gh",
        "git",
        "lifecycle_authority",
        "lifecycle_owner",
        "shell",
    }
)

_QUEUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,159}$")


@dataclass(frozen=True)
class TickWorkItem:
    handler: str
    key: str

    @classmethod
    def create(cls, *, handler: str, key: str) -> "TickWorkItem":
        return cls(handler=_validate_queue_token(handler, "handler"), key=_validate_queue_token(key, "key"))

    @classmethod
    def from_json(cls, row: Mapping[str, object]) -> "TickWorkItem":
        reject_command_payload(row)
        extra = set(row) - {"handler", "key"}
        if extra:
            raise ValueError(f"workqueue item must be key-only; unexpected fields={sorted(extra)}")
        handler = row.get("handler")
        key = row.get("key")
        if not isinstance(handler, str) or not isinstance(key, str):
            raise ValueError("workqueue item requires string handler and key")
        return cls.create(handler=handler, key=key)

    def to_json(self) -> dict[str, str]:
        return {"handler": self.handler, "key": self.key}


class KeyOnlyWorkQueue:
    def __init__(self, items: tuple[TickWorkItem, ...] = ()) -> None:
        self._queue: deque[TickWorkItem] = deque()
        self._keys: set[tuple[str, str]] = set()
        for item in items:
            self.enqueue(item.handler, item.key)

    def enqueue(self, handler: str, key: str) -> bool:
        item = TickWorkItem.create(handler=handler, key=key)
        identity = (item.handler, item.key)
        if identity in self._keys:
            return False
        self._keys.add(identity)
        self._queue.append(item)
        return True

    def enqueue_json(self, row: Mapping[str, object]) -> bool:
        item = TickWorkItem.from_json(row)
        return self.enqueue(item.handler, item.key)

    def dequeue(self) -> TickWorkItem | None:
        if not self._queue:
            return None
        item = self._queue.popleft()
        self._keys.discard((item.handler, item.key))
        return item

    def drain(self, *, limit: int | None = None) -> tuple[TickWorkItem, ...]:
        drained: list[TickWorkItem] = []
        while self._queue and (limit is None or len(drained) < limit):
            item = self.dequeue()
            if item is not None:
                drained.append(item)
        return tuple(drained)

    def keys(self) -> tuple[str, ...]:
        return tuple(f"{item.handler}:{item.key}" for item in self._queue)

    def empty(self) -> bool:
        return not self._queue

    def __len__(self) -> int:
        return len(self._queue)


def reject_command_payload(row: Mapping[str, object]) -> None:
    forbidden = sorted(FORBIDDEN_PAYLOAD_FIELDS & set(row))
    if forbidden:
        raise ValueError(f"workqueue item cannot carry command payload fields={forbidden}")


def _validate_queue_token(value: str, label: str) -> str:
    if not _QUEUE_TOKEN_RE.fullmatch(value):
        raise ValueError(f"invalid {label}: {value!r}")
    return value
