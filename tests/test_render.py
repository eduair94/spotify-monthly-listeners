import json

from helpers import TAYLOR, WEEKND, fake_id

from tracker.history import Entry, Row, entries_for_day
from tracker.render import (
    README_END,
    README_START,
    build_latest,
    cities_from_latest,
    dump_latest,
    escape_markdown,
    format_change,
    format_count,
    rank_entries,
    render_excerpt,
    render_leaderboard,
    replace_section,
)

DAY = "2026-11-01"


def entry(artist_id, name, listeners, followers=None, rank=None, c1=None, c1p=None, c7=None, c7p=None):
    return Entry(Row(DAY, artist_id, name, listeners, followers, rank), c1, c1p, c7, c7p)


def table_rows(markdown, heading=None):
    """The data rows of the first table (after ``heading`` when given)."""

    lines = markdown.splitlines()
    if heading:
        lines = lines[lines.index(heading) :]
    start = next(i for i, line in enumerate(lines) if line.startswith("|--"))
    rows = []
    for line in lines[start + 1 :]:
        if not line.startswith("|"):
            break
        rows.append(line)
    return rows


def test_number_formatting():
    assert format_count(116457297) == "116,457,297"
    assert format_count(None) == "—"
    assert format_change(12345, 0.0106) == "+12,345 (+0.01%)"
    assert format_change(-2000, -0.5) == "-2,000 (-0.50%)"
    assert format_change(0, 0.0) == "0 (0.00%)"
    assert format_change(5, None) == "+5"
    assert format_change(None, None) == "—"


def test_markdown_escaping():
    assert escape_markdown("$uicideboy$") == r"\$uicideboy\$"
    assert escape_markdown("a|b [c] *d* _e_ <f>") == r"a\|b \[c\] \*d\* \_e\_ \<f\>"
    assert escape_markdown("  two   spaces ") == "two spaces"


def test_ranking_puts_missing_values_last():
    ranked = rank_entries(
        [entry("a" * 22, "A", None), entry("b" * 22, "B", 5), entry("c" * 22, "C", 9), entry("d" * 22, "D", 5, followers=10)]
    )
    assert [e.row.name for e in ranked] == ["C", "D", "B", "A"]


def test_leaderboard_table():
    entries = [entry(fake_id(i), f"Artist {i}", 1000 + i, followers=i, rank=i or None) for i in range(150)]
    text = render_leaderboard(entries, day=DAY, tracked=150)
    rows = table_rows(text)
    assert len(rows) == 100
    assert rows[0] == f"| 1 | [Artist 149](https://open.spotify.com/artist/{fake_id(149)}) | 1,149 | — | — | 149 | 149 |"
    assert rows[-1].startswith("| 100 | [Artist 50]")
    assert "on **2026-11-01** (UTC), out of the 150 artists" in text
    assert "Most listeners gained" not in text  # no earlier day yet
    assert "have no value for" not in text


def test_leaderboard_with_changes_and_movers():
    entries = [
        entry(TAYLOR, "Taylor Swift", 100, c1=1, c1p=1.0, c7=10, c7p=11.1),
        entry(WEEKND, "The | Weeknd", 90, c1=-2, c1p=-2.2, c7=-30, c7p=-25.0),
        entry(fake_id(1), "Flat", 80, c1=0, c1p=0.0, c7=0, c7p=0.0),
    ]
    text = render_leaderboard(entries, day=DAY, tracked=4, not_updated=1, top=2)
    rows = table_rows(text)
    assert len(rows) == 2
    assert (
        rows[0] == f"| 1 | [Taylor Swift](https://open.spotify.com/artist/{TAYLOR}) | 100 | +1 (+1.00%) | +10 (+11.10%) | — | — |"
    )
    assert r"[The \| Weeknd]" in rows[1]
    gains = table_rows(text, "## Most listeners gained in 7 days")
    drops = table_rows(text, "## Most listeners lost in 7 days")
    assert len(gains) == 1 and "Taylor Swift" in gains[0]
    assert len(drops) == 1 and "Weeknd" in drops[0] and "-30 (-25.00%)" in drops[0]
    assert "1 tracked artist(s) have no value for 2026-11-01" in text


def test_movers_fall_back_to_one_day():
    text = render_leaderboard([entry(TAYLOR, "T", 100, c1=5, c1p=5.0)], day=DAY, tracked=1)
    assert "## Most listeners gained in 1 day" in text
    assert "## Most listeners lost" not in text


def test_latest_json():
    entries = [entry(WEEKND, "The Weeknd", 90, 5, 3, 1, 1.11111, None, None), entry(TAYLOR, "Taylor Swift", 100, 6, 1)]
    cities = {TAYLOR: [{"city": "London", "country": "GB", "region": "ENG", "listeners": 7}]}
    last = Row("2026-10-30", fake_id(1), "Gone Quiet", 5, 6, None)
    document = build_latest(entries, day=DAY, tracked=4, cities=cities, not_updated=[(fake_id(1), last), (fake_id(2), None)])
    text = dump_latest(document)
    parsed = json.loads(text)
    assert parsed == document
    assert text.endswith("}\n")
    assert parsed["date"] == DAY and parsed["tracked_artists"] == 4 and parsed["artists_with_data"] == 2
    first, second = parsed["artists"]
    assert (first["position"], first["id"], first["top_cities"][0]["city"]) == (1, TAYLOR, "London")
    assert (second["change_1d"], second["change_1d_pct"], second["change_7d"]) == (1, 1.1111, None)
    assert second["url"] == f"https://open.spotify.com/artist/{WEEKND}"
    assert parsed["not_updated"] == [
        {"id": fake_id(1), "name": "Gone Quiet", "last_date": "2026-10-30"},
        {"id": fake_id(2), "name": None, "last_date": None},
    ]
    # one artist per line, so a day's diff reads artist by artist
    assert sum(1 for line in text.splitlines() if line.lstrip().startswith('{"position"')) == 2


def test_latest_json_keeps_unicode_readable():
    text = dump_latest(build_latest([entry(TAYLOR, "ROSÉ", 1)], day=DAY, tracked=1))
    assert "ROSÉ" in text


def test_cities_are_reused_only_for_the_same_day():
    text = dump_latest(build_latest([entry(TAYLOR, "T", 1)], day=DAY, tracked=1, cities={TAYLOR: [{"city": "X"}]}))
    assert cities_from_latest(text, DAY) == {TAYLOR: [{"city": "X"}]}
    assert cities_from_latest(text, "2026-11-02") == {}
    assert cities_from_latest("not json", DAY) == {}


def test_readme_excerpt_between_markers():
    readme = f"# Title\n\nIntro\n\n{README_START}\nold table\n{README_END}\n\nMore text\n"
    excerpt = render_excerpt([entry(TAYLOR, "Taylor Swift", 100, c7=10, c7p=11.1)], day=DAY)
    updated = replace_section(readme, excerpt)
    assert "old table" not in updated
    assert updated.startswith("# Title\n\nIntro\n\n" + README_START + "\nTop 1 of the tracked artists on **2026-11-01**")
    assert updated.endswith(README_END + "\n\nMore text\n")
    assert f"| 1 | [Taylor Swift](https://open.spotify.com/artist/{TAYLOR}) | 100 | +10 (+11.10%) |" in updated
    assert replace_section(updated, excerpt) == updated


def test_readme_without_markers_is_left_alone():
    assert replace_section("# Title\n", "table") == "# Title\n"
    assert replace_section(f"{README_END} before {README_START}", "table") == f"{README_END} before {README_START}"


def test_entries_to_leaderboard_end_to_end():
    rows = [Row("2026-10-31", TAYLOR, "Taylor Swift", 99, 1, 6), Row(DAY, TAYLOR, "Taylor Swift", 100, 1, 6)]
    text = render_leaderboard(entries_for_day(rows, DAY, [TAYLOR]), day=DAY, tracked=1)
    assert table_rows(text)[0].endswith("| 100 | +1 (+1.01%) | — | 1 | 6 |")
