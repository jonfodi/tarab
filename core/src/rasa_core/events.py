"""Progress events. The fetcher only emits these; the CLI prints them and the app streams them to the UI."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable


@dataclass
class Event:
    kind: str           # see KINDS
    query: str          # "Artist - Title"
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return asdict(self)


KINDS = {
    "start",        # began working on a query
    "reference",    # official data found (data: lengths, sources, bpm)
    "search",       # searching Soulseek (data: text)
    "candidates",   # ranked results (data: count, top: [...])
    "skip",         # files filtered out and why
    "try",          # downloading from a candidate (data: candidate)
    "progress",     # transfer progress (data: state, percent)
    "rejected",     # a download failed a check or the transfer failed (data: reason)
    "identity",     # identity check result (data: verdict, note)
    "quality",      # transcode check result (data: claimed, verified, cliff)
    "parked",       # left waiting in a peer's queue; collected on a later run
    "lq",           # MP3 copy made
    "done",         # finished (data: Result.as_dict())
}

Sink = Callable[[Event], None]


def null_sink(_: Event) -> None:
    pass
