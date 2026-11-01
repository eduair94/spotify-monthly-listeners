"""Read the artist list (artists.txt).

One artist per line, written as a 22-character Spotify artist id, a
``spotify:artist:<id>`` URI or an ``https://open.spotify.com/artist/<id>`` URL
(``?si=`` and ``intl-xx/`` are fine). ``#`` starts a comment, on its own line or
after the artist. Blank lines are ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

_ID = r"[0-9A-Za-z]{22}"
ID_RE = re.compile(rf"^{_ID}$")
_URI_RE = re.compile(rf"^(?i:spotify:artist:)({_ID})$")
_URL_RE = re.compile(rf"^(?:https?://)?(?i:open\.spotify\.com/)(?:intl-[A-Za-z-]+/)?(?i:artist/)({_ID})(?:[/?].*)?$")


def parse_artist(entry: str) -> Optional[str]:
    """The artist id in one entry (id, URI or URL), or None if it is not one."""

    value = entry.strip()
    if ID_RE.match(value):
        return value
    match = _URI_RE.match(value) or _URL_RE.match(value)
    return match.group(1) if match else None


@dataclass
class ArtistList:
    """The ids in file order, plus what was wrong with the other lines."""

    ids: List[str] = field(default_factory=list)
    #: (line number, message) for lines that are not an artist: they are skipped.
    errors: List[Tuple[int, str]] = field(default_factory=list)
    #: (line number, message) for repeated artists: the first line counts.
    warnings: List[Tuple[int, str]] = field(default_factory=list)


def parse_artists(text: str) -> ArtistList:
    """Parse the contents of artists.txt."""

    result = ArtistList()
    first_line: Dict[str, int] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        entry = raw.split("#", 1)[0].strip()
        if not entry:
            continue
        artist_id = parse_artist(entry)
        if artist_id is None:
            shown = entry if len(entry) <= 80 else entry[:77] + "..."
            result.errors.append((number, f"not a Spotify artist id, URI or URL: {shown}"))
            continue
        if artist_id in first_line:
            result.warnings.append((number, f"{artist_id} is already on line {first_line[artist_id]}"))
            continue
        first_line[artist_id] = number
        result.ids.append(artist_id)
    return result


def read_artists(path: Union[str, Path]) -> ArtistList:
    """Read and parse an artist list file (UTF-8, with or without a BOM)."""

    return parse_artists(Path(path).read_text(encoding="utf-8-sig"))
