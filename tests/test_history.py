import pytest
from helpers import TAYLOR, WEEKND

from tracker.fetch import ArtistStats
from tracker.history import (
    Row,
    change,
    entries_for_day,
    format_history,
    last_rows,
    latest_day,
    read_history,
    rows_for_day,
    upsert,
    write_history,
)


def row(day, artist_id, listeners, name="Artist", followers=None, rank=None):
    return Row(day, artist_id, name, listeners, followers, rank)


# --- the CSV file -------------------------------------------------------------


def test_round_trip_with_empty_values(tmp_path):
    path = tmp_path / "history.csv"
    rows = [
        row("2026-11-01", TAYLOR, 100388733, "Taylor Swift", 166079303, 6),
        row("2026-11-01", WEEKND, None, 'The "Weeknd", Inc', None, None),
    ]
    assert write_history(path, rows) is True
    text = path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "date,artist_id,name,monthly_listeners,followers,world_rank"
    assert "\r" not in text
    assert f'2026-11-01,{WEEKND},"The ""Weeknd"", Inc",,,' in text
    assert read_history(path) == sorted(rows, key=lambda r: (r.date, r.artist_id))


def test_missing_file_reads_as_empty(tmp_path):
    assert read_history(tmp_path / "nope.csv") == []


def test_reads_a_file_saved_with_bom_and_crlf(tmp_path):
    path = tmp_path / "history.csv"
    text = f"date,artist_id,name,monthly_listeners,followers,world_rank\r\n2026-11-01,{TAYLOR},Taylor Swift,5,6,\r\n"
    path.write_bytes(("\ufeff" + text).encode("utf-8"))
    assert read_history(path) == [row("2026-11-01", TAYLOR, 5, "Taylor Swift", 6, None)]


def test_rejects_a_file_without_the_expected_columns(tmp_path):
    path = tmp_path / "history.csv"
    path.write_text("date,artist\n2026-11-01,x\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_history(path)


@pytest.mark.parametrize(
    "line, message",
    [
        (f"Nov 1 2026,{TAYLOR},T,5,6,", "line 2: date is not YYYY-MM-DD"),
        (f"2026-11-01,{TAYLOR},T,5,six,", "line 2: followers is not a whole number"),
        (f"2026-11-01,{TAYLOR},{'x' * 200_000},5,6,", "line 2: field larger than field limit"),
    ],
    ids=["date", "number", "csv"],  # short ids: the long row would not fit in PYTEST_CURRENT_TEST on Windows
)
def test_rejects_rows_it_cannot_read(tmp_path, line, message):
    path = tmp_path / "history.csv"
    path.write_text("date,artist_id,name,monthly_listeners,followers,world_rank\n" + line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        read_history(path)


def test_rows_are_sorted_by_date_then_artist():
    text = format_history([row("2026-11-02", "b" * 22, 1), row("2026-11-01", "b" * 22, 1), row("2026-11-02", "a" * 22, 1)])
    keys = [line.split(",")[:2] for line in text.splitlines()[1:]]
    assert keys == [["2026-11-01", "b" * 22], ["2026-11-02", "a" * 22], ["2026-11-02", "b" * 22]]


# --- recording a day ----------------------------------------------------------


def test_recording_the_same_day_twice_changes_nothing(tmp_path):
    path = tmp_path / "history.csv"
    day = [row("2026-11-01", TAYLOR, 100), row("2026-11-01", WEEKND, 90)]
    write_history(path, upsert(read_history(path), day))
    first = path.read_bytes()
    assert write_history(path, upsert(read_history(path), day)) is False
    assert path.read_bytes() == first
    assert len(read_history(path)) == 2


def test_a_rerun_replaces_that_day_and_keeps_the_rest():
    old = [row("2026-10-31", TAYLOR, 99), row("2026-11-01", TAYLOR, 100), row("2026-11-01", WEEKND, 90)]
    merged = upsert(old, [row("2026-11-01", TAYLOR, 101)])
    assert merged == [row("2026-10-31", TAYLOR, 99), row("2026-11-01", TAYLOR, 101), row("2026-11-01", WEEKND, 90)]


def test_appending_a_new_day_only_adds_lines_at_the_end(tmp_path):
    path = tmp_path / "history.csv"
    write_history(path, [row("2026-11-01", TAYLOR, 100), row("2026-11-01", WEEKND, 90)])
    before = path.read_text(encoding="utf-8")
    write_history(path, upsert(read_history(path), [row("2026-11-02", WEEKND, 91), row("2026-11-02", TAYLOR, 101)]))
    after = path.read_text(encoding="utf-8")
    assert after.startswith(before)
    assert after[len(before) :].splitlines() == [f"2026-11-02,{TAYLOR},Artist,101,,", f"2026-11-02,{WEEKND},Artist,91,,"]


def test_rows_for_day_from_stats():
    stats = [ArtistStats(TAYLOR, "Taylor Swift", 100, 200, 6)]
    assert rows_for_day("2026-11-01", stats) == [Row("2026-11-01", TAYLOR, "Taylor Swift", 100, 200, 6)]


# --- changes ------------------------------------------------------------------


@pytest.mark.parametrize(
    "current, previous, expected",
    [
        (110, 100, (10, 10.0)),
        (90, 100, (-10, -10.0)),
        (100, 100, (0, 0.0)),
        (5, 0, (5, None)),
        (None, 100, (None, None)),
        (100, None, (None, None)),
    ],
)
def test_change(current, previous, expected):
    assert change(current, previous) == expected


def test_changes_compare_exactly_one_and_seven_days_earlier():
    rows = [
        row("2026-10-25", TAYLOR, 1000),
        row("2026-10-31", TAYLOR, 1080),
        row("2026-11-01", TAYLOR, 1100),
        row("2026-10-30", WEEKND, 500),  # two days earlier: not a 1-day change
        row("2026-11-01", WEEKND, 520),
    ]
    taylor, weeknd = entries_for_day(rows, "2026-11-01")
    assert taylor.row.artist_id == TAYLOR
    assert (taylor.change_1d, taylor.change_7d) == (20, 100)
    assert taylor.change_1d_pct == pytest.approx(20 / 1080 * 100)
    assert taylor.change_7d_pct == pytest.approx(10.0)
    assert (weeknd.change_1d, weeknd.change_7d, weeknd.change_1d_pct) == (None, None, None)


def test_entries_follow_the_given_artists_and_skip_those_without_a_row():
    rows = [row("2026-11-01", TAYLOR, 1), row("2026-11-01", WEEKND, 2), row("2026-10-31", "c" * 22, 3)]
    entries = entries_for_day(rows, "2026-11-01", [WEEKND, "c" * 22, TAYLOR])
    assert [entry.row.artist_id for entry in entries] == [WEEKND, TAYLOR]


def test_latest_day_and_last_rows():
    rows = [row("2026-10-31", TAYLOR, 1), row("2026-11-01", TAYLOR, 2), row("2026-11-02", WEEKND, 3)]
    assert latest_day(rows) == "2026-11-02"
    assert latest_day(rows, [TAYLOR]) == "2026-11-01"
    assert latest_day([], [TAYLOR]) is None
    assert last_rows(rows)[TAYLOR].monthly_listeners == 2
