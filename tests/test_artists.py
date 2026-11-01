from helpers import ROOT, TAYLOR, WEEKND

from tracker.artists import parse_artist, parse_artists, read_artists

TRACK = "0VjIjW4GlUZAMYd2vXMi3b"


def test_accepts_ids_uris_and_urls():
    assert parse_artist(TAYLOR) == TAYLOR
    assert parse_artist(f"  {TAYLOR}  ") == TAYLOR
    assert parse_artist(f"spotify:artist:{TAYLOR}") == TAYLOR
    assert parse_artist(f"https://open.spotify.com/artist/{TAYLOR}") == TAYLOR
    assert parse_artist(f"https://open.spotify.com/artist/{TAYLOR}?si=Ab12Cd34") == TAYLOR
    assert parse_artist(f"https://open.spotify.com/intl-es/artist/{TAYLOR}") == TAYLOR
    assert parse_artist(f"http://open.spotify.com/artist/{TAYLOR}/") == TAYLOR
    assert parse_artist(f"open.spotify.com/artist/{TAYLOR}") == TAYLOR


def test_rejects_anything_that_is_not_an_artist():
    assert parse_artist(f"https://open.spotify.com/track/{TRACK}") is None
    assert parse_artist(f"spotify:track:{TRACK}") is None
    assert parse_artist(TAYLOR[:-1]) is None  # 21 characters
    assert parse_artist(TAYLOR + "x") is None  # 23 characters
    assert parse_artist("Taylor Swift") is None
    assert parse_artist(f"https://example.com/artist/{TAYLOR}") is None
    assert parse_artist("") is None


def test_comments_blank_lines_duplicates_and_errors():
    text = "\n".join(
        [
            "# a comment line",
            "",
            f"{TAYLOR}  # Taylor Swift",
            "   ",
            f"spotify:artist:{WEEKND}",
            f"https://open.spotify.com/artist/{TAYLOR}?si=x  # the same artist again",
            "not an artist",
            f"https://open.spotify.com/track/{TRACK}",
        ]
    )
    result = parse_artists(text)
    assert result.ids == [TAYLOR, WEEKND]
    assert [line for line, _ in result.warnings] == [6]
    assert "line 3" in result.warnings[0][1]
    assert [line for line, _ in result.errors] == [7, 8]


def test_order_is_kept():
    ids = [WEEKND, TAYLOR]
    assert parse_artists("\n".join(ids)).ids == ids


def test_reads_utf8_with_bom_and_crlf(tmp_path):
    path = tmp_path / "artists.txt"
    path.write_bytes(("\ufeff# list\r\n" + TAYLOR + "  # Taylor\r\n" + WEEKND + "\r\n").encode("utf-8"))
    assert read_artists(path).ids == [TAYLOR, WEEKND]


def test_repository_artist_list_is_clean():
    result = read_artists(ROOT / "artists.txt")
    assert result.errors == []
    assert result.warnings == []
    assert 400 <= len(result.ids) <= 600
