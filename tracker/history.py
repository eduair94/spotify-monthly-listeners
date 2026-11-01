"""data/history.csv: one row per artist per UTC day, and the changes between days.

The file is kept sorted by date, then artist id, so each day's rows are appended
at the end. Recording a day that is already there replaces those artists' rows
for that day instead of adding new ones: re-running the tracker on the same day
never duplicates rows.
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

FIELDS = ("date", "artist_id", "name", "monthly_listeners", "followers", "world_rank")


@dataclass(frozen=True)
class Row:
    date: str  # YYYY-MM-DD (UTC)
    artist_id: str
    name: str
    monthly_listeners: Optional[int]
    followers: Optional[int]
    world_rank: Optional[int]


def _number(text: Optional[str], *, line: int, column: str) -> Optional[int]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"history.csv line {line}: {column} is not a whole number: {text!r}") from None


def read_history(path: Union[str, Path]) -> List[Row]:
    """Every row of the history file (an empty list when it does not exist yet)."""

    file = Path(path)
    if not file.exists():
        return []
    with file.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        absent = [name for name in FIELDS if name not in (reader.fieldnames or [])]
        if absent:
            raise ValueError(f"{file}: missing column(s): {', '.join(absent)}")
        rows = []
        for record in reader:
            day = (record.get("date") or "").strip()
            if not day:
                continue
            line = reader.line_num
            rows.append(
                Row(
                    date=day,
                    artist_id=(record.get("artist_id") or "").strip(),
                    name=record.get("name") or "",
                    monthly_listeners=_number(record.get("monthly_listeners"), line=line, column="monthly_listeners"),
                    followers=_number(record.get("followers"), line=line, column="followers"),
                    world_rank=_number(record.get("world_rank"), line=line, column="world_rank"),
                )
            )
        return rows


def _cell(value: Optional[int]) -> str:
    return "" if value is None else str(value)


def sort_rows(rows: Iterable[Row]) -> List[Row]:
    return sorted(rows, key=lambda row: (row.date, row.artist_id))


def format_history(rows: Iterable[Row]) -> str:
    """The CSV text: header, then rows by date and artist id, ``\\n`` line ends."""

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(FIELDS)
    for row in sort_rows(rows):
        writer.writerow(
            [row.date, row.artist_id, row.name, _cell(row.monthly_listeners), _cell(row.followers), _cell(row.world_rank)]
        )
    return buffer.getvalue()


def write_text_if_changed(path: Union[str, Path], text: str) -> bool:
    """Write ``text`` (UTF-8, ``\\n`` line ends) unless the file already holds it. True if written."""

    file = Path(path)
    if file.exists() and file.read_bytes() == text.encode("utf-8"):
        return False
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(file.name + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.replace(temporary, file)
    return True


def write_history(path: Union[str, Path], rows: Iterable[Row]) -> bool:
    return write_text_if_changed(path, format_history(rows))


def upsert(rows: Iterable[Row], new_rows: Iterable[Row]) -> List[Row]:
    """``rows`` plus ``new_rows``; a new row replaces the row of the same artist and day."""

    merged: Dict[Tuple[str, str], Row] = {(row.date, row.artist_id): row for row in rows}
    for row in new_rows:
        merged[(row.date, row.artist_id)] = row
    return sort_rows(merged.values())


def rows_for_day(day: str, stats: Iterable[Any]) -> List[Row]:
    """History rows for ``day`` from fetched stats (objects with the ArtistStats fields)."""

    return [
        Row(
            date=day,
            artist_id=item.id,
            name=item.name,
            monthly_listeners=item.monthly_listeners,
            followers=item.followers,
            world_rank=item.world_rank,
        )
        for item in stats
    ]


# --- changes -----------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    """An artist's row for one day with its monthly listener changes."""

    row: Row
    change_1d: Optional[int]
    change_1d_pct: Optional[float]
    change_7d: Optional[int]
    change_7d_pct: Optional[float]


def change(current: Optional[int], previous: Optional[int]) -> Tuple[Optional[int], Optional[float]]:
    """``(difference, percent)``; None where a value is missing (percent also when previous is 0)."""

    if current is None or previous is None:
        return None, None
    difference = current - previous
    return difference, (difference / previous * 100.0 if previous else None)


def index_rows(rows: Iterable[Row]) -> Dict[str, Dict[str, Row]]:
    """``{artist_id: {date: row}}``."""

    index: Dict[str, Dict[str, Row]] = {}
    for row in rows:
        index.setdefault(row.artist_id, {})[row.date] = row
    return index


def days_before(day: str, days: int) -> str:
    return (date.fromisoformat(day) - timedelta(days=days)).isoformat()


def entries_for_day(rows: Iterable[Row], day: str, artist_ids: Optional[Sequence[str]] = None) -> List[Entry]:
    """Each artist's row on ``day`` with its change since exactly 1 and 7 days earlier.

    Only ``artist_ids`` are included when given (in that order); artists without
    a row on ``day`` are left out.
    """

    index = index_rows(rows)
    day_1, day_7 = days_before(day, 1), days_before(day, 7)
    ids = list(artist_ids) if artist_ids is not None else sorted(index)
    entries = []
    for artist_id in ids:
        days = index.get(artist_id, {})
        row = days.get(day)
        if row is None:
            continue
        before_1, before_7 = days.get(day_1), days.get(day_7)
        c1 = change(row.monthly_listeners, before_1.monthly_listeners if before_1 else None)
        c7 = change(row.monthly_listeners, before_7.monthly_listeners if before_7 else None)
        entries.append(Entry(row, c1[0], c1[1], c7[0], c7[1]))
    return entries


def latest_day(rows: Iterable[Row], artist_ids: Optional[Sequence[str]] = None) -> Optional[str]:
    """The most recent date with a row (for ``artist_ids`` only, when given)."""

    wanted = set(artist_ids) if artist_ids is not None else None
    days = [row.date for row in rows if wanted is None or row.artist_id in wanted]
    return max(days) if days else None


def last_rows(rows: Iterable[Row]) -> Dict[str, Row]:
    """Each artist's most recent row."""

    out: Dict[str, Row] = {}
    for row in rows:
        current = out.get(row.artist_id)
        if current is None or row.date >= current.date:
            out[row.artist_id] = row
    return out
