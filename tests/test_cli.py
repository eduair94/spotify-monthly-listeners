import json

import pytest
from helpers import TAYLOR, WEEKND, FakeApi, FakeSession, artist_object, fake_id

from tracker.cli import Console, main
from tracker.history import read_history
from tracker.render import README_END, README_START

KEY = "secret-test-key-0123456789"


@pytest.fixture
def repo(tmp_path):
    ids = [TAYLOR, WEEKND] + [fake_id(i) for i in range(60)]
    (tmp_path / "artists.txt").write_text(
        "# test list\n" + "\n".join(f"{artist_id}  # artist" for artist_id in ids) + "\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text(f"# Readme\n\n{README_START}\nplaceholder\n{README_END}\n", encoding="utf-8")
    return tmp_path


def universe(bump=0):
    artists = [
        artist_object(TAYLOR, "Taylor Swift", 100_000_000 + bump, 166_000_000, 6),
        artist_object(WEEKND, "The Weeknd", 116_000_000 + bump, 129_000_000, 3),
    ]
    artists += [artist_object(fake_id(i), f"Artist {i}", 1_000 + i + bump, 10 * i) for i in range(60)]
    return artists


def run(repo, *args, env=None, session=None):
    lines = []
    environment = {"SPOTIFY_API_KEY": KEY} if env is None else env
    code = main(["run", "--root", str(repo), "--pause", "0", *args], env=environment, session=session, write=lines.append)
    return code, "\n".join(lines)


def test_without_a_key_it_skips_cleanly(repo):
    code, output = run(repo, env={})
    assert code == 0
    assert "SPOTIFY_API_KEY is not set" in output
    assert not (repo / "data").exists() and not (repo / "LEADERBOARD.md").exists()


def test_the_skip_is_a_notice_in_github_actions(repo):
    code, output = run(repo, env={"GITHUB_ACTIONS": "true"})
    assert code == 0 and output.startswith("::notice::SPOTIFY_API_KEY is not set")


def test_annotations_are_escaped_and_plain_output_is_not():
    lines = []
    Console(lines.append, github=True).warning("50% done\nnext")
    Console(lines.append, github=False).warning("50% done")
    assert lines == ["::warning::50%25 done%0Anext", "warning: 50% done"]


def test_a_run_writes_every_file_and_never_prints_the_key(repo):
    session = FakeSession(FakeApi(universe(), slow={fake_id(7)}))
    code, output = run(repo, "--date", "2026-11-01", session=session)
    assert code == 0, output
    assert KEY not in output
    assert len(session.requests) == 3  # 50 + 12, then the one artist whose stats were late
    assert all(r["headers"]["x-rapidapi-key"] == KEY for r in session.requests)

    rows = read_history(repo / "data" / "history.csv")
    assert len(rows) == 62 and {r.date for r in rows} == {"2026-11-01"}
    latest = json.loads((repo / "data" / "latest.json").read_text(encoding="utf-8"))
    assert latest["artists"][0]["name"] == "The Weeknd" and latest["artists"][0]["top_cities"]
    leaderboard = (repo / "LEADERBOARD.md").read_text(encoding="utf-8")
    assert (
        f"| 1 | [The Weeknd](https://open.spotify.com/artist/{WEEKND}) | 116,000,000 | — | — | 129,000,000 | 3 |" in leaderboard
    )
    readme = (repo / "README.md").read_text(encoding="utf-8")
    assert "placeholder" not in readme and "| 2 | [Taylor Swift]" in readme
    assert "Recorded 62 of 62 artists for 2026-11-01 with 3 request(s)." in output


def test_rerunning_the_same_day_is_idempotent(repo):
    run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe())))
    files = ["data/history.csv", "data/latest.json", "LEADERBOARD.md", "README.md"]
    before = {name: (repo / name).read_bytes() for name in files}
    code, output = run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe())))
    assert code == 0 and "No file changed." in output
    assert {name: (repo / name).read_bytes() for name in files} == before


def test_the_next_day_adds_rows_and_changes(repo):
    run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe())))
    code, _ = run(repo, "--date", "2026-11-02", session=FakeSession(FakeApi(universe(bump=500))))
    assert code == 0
    rows = read_history(repo / "data" / "history.csv")
    assert len(rows) == 124
    leaderboard = (repo / "LEADERBOARD.md").read_text(encoding="utf-8")
    assert "| 116,000,500 | +500 (+0.00%) | — |" in leaderboard
    assert "## Most listeners gained in 1 day" in leaderboard
    latest = json.loads((repo / "data" / "latest.json").read_text(encoding="utf-8"))
    assert latest["date"] == "2026-11-02" and latest["artists"][0]["change_1d"] == 500


def test_artists_missing_today_are_listed_and_keep_their_history(repo):
    run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe())))
    api = FakeApi([a for a in universe() if a["id"] != TAYLOR])
    code, output = run(repo, "--date", "2026-11-02", "--retry-passes", "0", session=FakeSession(api))
    assert code == 0
    assert "1 artist(s) got no stats this run" in output
    latest = json.loads((repo / "data" / "latest.json").read_text(encoding="utf-8"))
    assert latest["not_updated"] == [{"id": TAYLOR, "name": "Taylor Swift", "last_date": "2026-11-01"}]
    assert "1 tracked artist(s) have no value for 2026-11-02" in (repo / "LEADERBOARD.md").read_text(encoding="utf-8")


def test_a_refused_key_fails_without_writing(repo):
    session = FakeSession(status=403, error_body={"message": "You are not subscribed to this API."})
    code, output = run(repo, "--retries", "0", session=session)
    assert code == 1
    assert "refused the key" in output and KEY not in output
    assert len(session.requests) == 1
    assert not (repo / "data" / "history.csv").exists()


def test_the_request_limit_comes_from_the_environment(repo):
    session = FakeSession(FakeApi(universe()))
    code, output = run(repo, env={"SPOTIFY_API_KEY": KEY, "TRACKER_MAX_REQUESTS": "1"}, session=session)
    assert code == 0
    assert len(session.requests) == 1
    assert "this run may send 1" in output and "limit of 1 requests" in output


@pytest.mark.parametrize("env", [{"SPOTIFY_API_BASE": "not a url"}, {"TRACKER_MAX_REQUESTS": "many"}])
def test_bad_settings_fail_fast(repo, env):
    session = FakeSession(FakeApi(universe()))
    code, output = run(repo, env={"SPOTIFY_API_KEY": KEY, **env}, session=session)
    assert code == 1 and session.requests == []


def test_render_rebuilds_the_same_files(repo):
    run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe())))
    leaderboard = repo / "LEADERBOARD.md"
    before = leaderboard.read_bytes()
    leaderboard.unlink()
    lines = []
    assert main(["render", "--root", str(repo)], env={}, write=lines.append) == 0
    assert leaderboard.read_bytes() == before
    assert "Rendered 2026-11-01" in lines[-1]


def test_check(repo):
    lines = []
    assert main(["check", "--root", str(repo)], env={}, write=lines.append) == 0
    assert "62 artists, 2 request(s) per daily run" in lines[-1]
    with (repo / "artists.txt").open("a", encoding="utf-8") as handle:
        handle.write("https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b\n")
    lines.clear()
    assert main(["check", "--root", str(repo)], env={}, write=lines.append) == 1
    assert any("not a Spotify artist" in line for line in lines)
