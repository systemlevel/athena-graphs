"""Checkpoint stores used by interrupt/resume workflows."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from threading import Lock
from typing import Protocol

from .types import Checkpoint


class Checkpointer(Protocol):
    def save(self, thread_id: str, checkpoint: Checkpoint) -> None: ...
    def load(self, thread_id: str) -> Checkpoint | None: ...
    def delete(self, thread_id: str) -> None: ...


class MemoryCheckpointer:
    """Process-local checkpoints; useful for servers, notebooks, and tests."""

    def __init__(self) -> None:
        self._items: dict[str, Checkpoint] = {}
        self._lock = Lock()

    def save(self, thread_id: str, checkpoint: Checkpoint) -> None:
        with self._lock:
            self._items[thread_id] = copy.deepcopy(checkpoint)

    def load(self, thread_id: str) -> Checkpoint | None:
        with self._lock:
            value = self._items.get(thread_id)
            return copy.deepcopy(value) if value is not None else None

    def delete(self, thread_id: str) -> None:
        with self._lock:
            self._items.pop(thread_id, None)


class JsonFileCheckpointer:
    """Durable, atomic JSON checkpoints stored one file per thread."""

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def _path(self, thread_id: str) -> Path:
        if not thread_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for ch in thread_id):
            raise ValueError("thread_id may contain only letters, numbers, '-', '_', and '.'")
        return self.directory / f"{thread_id}.json"

    def save(self, thread_id: str, checkpoint: Checkpoint) -> None:
        target = self._path(thread_id)
        with self._lock:
            fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=self.directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(checkpoint.to_dict(), handle, indent=2, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            except BaseException:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                raise

    def load(self, thread_id: str) -> Checkpoint | None:
        path = self._path(thread_id)
        with self._lock:
            if not path.exists():
                return None
            with path.open("r", encoding="utf-8") as handle:
                return Checkpoint.from_dict(json.load(handle))

    def delete(self, thread_id: str) -> None:
        with self._lock:
            try:
                self._path(thread_id).unlink()
            except FileNotFoundError:
                pass
