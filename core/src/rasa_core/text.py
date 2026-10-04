"""Text normalisation, matching heuristics and query parsing.

Every rule here came from a real miss: see tests/test_text.py for the cases.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .identity import Reference

# Words that don't identify a track.
STOP = {"the", "a", "and", "feat", "ft", "featuring", "original", "mix", "extended", "x", "vs", "presents", "pres"}

# Versions you only get if you ask for them (the word must appear in your query).
VARIANTS = {"remix", "rmx", "bootleg", "edit", "radio", "acapella", "acappella", "instrumental",
            "live", "karaoke", "cover", "rework", "mashup", "vip", "dub", "reprise", "remake", "sped", "slowed"}

# Words that mark a bracketed "(Someone's Overdub)" / "[XYZ Mix]" tag as a version name.
VERSION_WORDS = {"mix", "dub", "edit", "remix", "rmx", "version", "rework", "overdub", "vip", "bootleg", "remaster"}

ROMAN = {"ii": "2", "iii": "3", "iv": "4", "vi": "6", "vii": "7", "viii": "8", "ix": "9"}

_GENERIC_VERSION = re.compile(r"\((original|extended)( mix)?\)", re.I)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace("'", "")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def tokens(s: str) -> set[str]:
    """Normalised words, with roman numerals as digits ("Part II" == "Part 2")."""
    return {ROMAN.get(w, w) for w in norm(s).split()}


def compact(s: str) -> str:
    return norm(s).replace(" ", "")


def compact_in(title: str, name: str) -> bool:
    """Title appears in name as a run of whole words, ignoring spaces/punctuation: "Page-R" ~ "Pager", but
    "Untitled 1" ≠ "Untitled 10" and "Waitin'" ≠ "Waiting For You"."""
    target, words = compact(title), norm(name).split()
    if not target:
        return False
    for i in range(len(words)):
        run = ""
        for w in words[i:]:
            run += w
            if run == target:
                return True
            if len(run) >= len(target):
                break
    return False


def other_version(name: str, asked: set[str]) -> bool:
    """A bracketed version tag the query didn't ask for, e.g. "(Stojche's Overdub)"."""
    for tag in re.findall(r"[(\[]([^)\]]+)[)\]]", name):
        t = tokens(tag)
        if t & VERSION_WORDS and not t <= {"original", "extended", "mix", "version", "remaster", "remastered"} \
                and not (t - VERSION_WORDS) <= asked:
            return True
    return False


def basename(remote: str) -> str:
    """Last path segment of a Soulseek path (they use backslashes)."""
    return re.split(r"[\\/]", remote)[-1]


def stem(name: str) -> str:
    return re.sub(r"\.[^.]+$", "", name)


def fmt_len(seconds: float | int | None) -> str:
    if not seconds:
        return "?:??"
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


@dataclass
class Query:
    artist: str
    title: str
    length: int | None = None    # "Artist - Title | 8:54": the exact version wanted
    catalog: str | None = None   # "Artist - Title [SUSH31]": the release it's from
    ref: Reference | None = field(default=None, compare=False, repr=False)  # preset by EP fetches

    @classmethod
    def parse(cls, line: str, title_first: bool = False) -> Query:
        length = None
        if m := re.search(r"\|\s*(\d+):(\d{2})\s*$", line):
            length, line = int(m[1]) * 60 + int(m[2]), line[: m.start()]
        line = line.strip().rstrip(" -")
        if " - " not in line:
            raise ValueError(f'expected "Artist - Title", got: {line!r}')
        a, t = line.split(" - ", 1)
        if title_first:
            a, t = t, a
        catalog = None
        if m := re.search(r"\s*\[([A-Za-z]{2,}[\s-]?\d+[A-Za-z]?)\]\s*$", t):   # [SUSH31], [LOVE 064]
            catalog, t = m[1], t[: m.start()]
        return cls(a.strip(), t.strip(), length, catalog)

    @property
    def search_text(self) -> str:
        """What to type into Soulseek, which matches whole words.

        - generic "(Original Mix)" noise is dropped (files often omit it)
        - stopwords are dropped from the artist only; in titles they matter ("On & On" -> "on and on")
        - apostrophes split words: Soulseek indexes "D'Arcangelo" as "d" + "arcangelo"
        """
        t = _GENERIC_VERSION.sub("", self.title)
        words = [w for w in norm(self.artist.replace("'", " ")).split() if len(w) > 1 and w not in STOP] + \
                [w for w in norm(t.replace("'", " ")).split() if w not in {"original", "extended", "mix", "feat", "ft", "featuring"}]
        return " ".join(dict.fromkeys(words))

    @property
    def title_words(self) -> list[str]:
        return [w for w in norm(self.title.replace("'", " ")).split() if w not in STOP]

    @property
    def all_tokens(self) -> set[str]:
        """Everything the query asked for. "(Ian Pooley Mix)" also accepts files filed as "Remix"."""
        t = tokens(self.artist) | tokens(self.title)
        if "mix" in tokens(_GENERIC_VERSION.sub("", self.title)):
            t |= {"remix", "rmx"}
        return t

    @property
    def key(self) -> str:
        """Stable identity for the database: same track + same version."""
        return f"{compact(self.artist)}|{compact(_GENERIC_VERSION.sub('', self.title))}"

    def same_title(self, name: str) -> bool:
        """`name` is this track *and* this version (a remix you have isn't the original you asked for)."""
        t = tokens(name)
        return (tokens(self.title) - STOP <= t or compact_in(self.title, name)) \
            and not (t & VARIANTS) - self.all_tokens and not other_version(name, self.all_tokens)

    def __str__(self) -> str:
        return f"{self.artist} - {self.title}"


def parse_list(text: str, title_first: bool = False) -> list[Query]:
    """A pasted list, one track per line. A header line "TITLE - ARTIST" / "ARTIST - TITLE" sets column order.
    Blank lines and #comments are skipped."""
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    if lines and re.fullmatch(r"(title|artist)\s*-\s*(title|artist)", lines[0], re.I):
        title_first = lines.pop(0).lower().startswith("title")
    return [Query.parse(l, title_first) for l in lines]
