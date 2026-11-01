"""Command line: ``python -m tracker [run|render|check]``.

run     fetch today's numbers for artists.txt, append them to data/history.csv,
        then write LEADERBOARD.md, data/latest.json and the README excerpt
render  rewrite those files from data/history.csv without calling the API
check   validate artists.txt and show how many requests a daily run takes
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from . import __version__
from .artists import ArtistList, read_artists
from .fetch import (
    BATCH_SIZE,
    FetchBatch,
    FetchReport,
    RequestBudget,
    fetch_stats,
    make_client,
    requests_needed,
    resolve_api,
    sdk_fetcher,
)
from .history import (
    Row,
    entries_for_day,
    last_rows,
    latest_day,
    read_history,
    rows_for_day,
    upsert,
    write_history,
    write_text_if_changed,
)
from .render import build_latest, cities_from_latest, dump_latest, render_excerpt, render_leaderboard, replace_section

#: Requests per run, retries included, unless TRACKER_MAX_REQUESTS or --max-requests says
#: otherwise. 30 a day is at most 930 in a 31-day month: under the free RapidAPI plan's 1,000.
DEFAULT_MAX_REQUESTS = 30

#: Minutes after which a run starts no new request (--time-limit). daily.yml gives the job
#: 30 minutes, which leaves room for the last request, the install and the commit.
DEFAULT_TIME_LIMIT = 20.0


class Console:
    """Log lines; inside GitHub Actions, warnings and errors also become annotations."""

    def __init__(self, write: Callable[[str], None] = print, github: bool = False) -> None:
        self._write = write
        self._github = github

    def info(self, message: str) -> None:
        self._write(message)

    def notice(self, message: str) -> None:
        self._annotate("notice", message, message)

    def warning(self, message: str) -> None:
        self._annotate("warning", message, f"warning: {message}")

    def error(self, message: str) -> None:
        self._annotate("error", message, f"error: {message}")

    def _annotate(self, kind: str, message: str, plain: str) -> None:
        if not self._github:
            self._write(plain)
            return
        escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        self._write(f"::{kind}::{escaped}")


@dataclass
class Paths:
    artists: Path
    history: Path
    latest: Path
    leaderboard: Path
    readme: Path

    @classmethod
    def at(cls, root: Path, artists: Optional[Path] = None) -> Paths:
        return cls(
            artists=artists or root / "artists.txt",
            history=root / "data" / "history.csv",
            latest=root / "data" / "latest.json",
            leaderboard=root / "LEADERBOARD.md",
            readme=root / "README.md",
        )


def load_artists(paths: Paths, console: Console) -> Optional[ArtistList]:
    if not paths.artists.exists():
        console.error(f"{paths.artists} does not exist")
        return None
    artists = read_artists(paths.artists)
    for line, message in artists.errors:
        console.warning(f"{paths.artists.name} line {line}: {message} (skipped)")
    for line, message in artists.warnings:
        console.warning(f"{paths.artists.name} line {line}: {message} (skipped)")
    return artists


def render_outputs(
    paths: Paths,
    rows: Sequence[Row],
    artist_ids: Sequence[str],
    day: str,
    cities: Mapping[str, List[Dict[str, Any]]],
    *,
    top: int = 100,
) -> List[str]:
    """Write LEADERBOARD.md and the README excerpt for ``day``. Returns what changed.

    data/latest.json is written only when ``day`` is the latest day in the history:
    it also holds the top cities, which history.csv does not keep, so an earlier
    day must never replace it.
    """

    entries = entries_for_day(rows, day, artist_ids)
    have = {entry.row.artist_id for entry in entries}
    last = last_rows(rows)
    not_updated = [(artist_id, last.get(artist_id)) for artist_id in artist_ids if artist_id not in have]
    changed = []
    leaderboard = render_leaderboard(entries, day=day, tracked=len(artist_ids), top=top, not_updated=len(not_updated))
    if write_text_if_changed(paths.leaderboard, leaderboard):
        changed.append(paths.leaderboard.name)
    if day == latest_day(rows, artist_ids):
        latest = build_latest(entries, day=day, tracked=len(artist_ids), cities=cities, not_updated=not_updated)
        if write_text_if_changed(paths.latest, dump_latest(latest)):
            changed.append("data/" + paths.latest.name)
    if paths.readme.exists():
        text = paths.readme.read_text(encoding="utf-8")
        updated = replace_section(text, render_excerpt(entries, day=day))
        if updated != text and write_text_if_changed(paths.readme, updated):
            changed.append(paths.readme.name)
    return changed


def _stored_cities(paths: Paths, day: str) -> Dict[str, List[Dict[str, Any]]]:
    if not paths.latest.exists():
        return {}
    return cities_from_latest(paths.latest.read_text(encoding="utf-8"), day)


def record_and_render(
    paths: Paths,
    artist_ids: Sequence[str],
    day: str,
    report: FetchReport,
    console: Console,
    *,
    history: Sequence[Row],
    top: int = 100,
) -> int:
    """Add a fetch report's numbers to ``history`` for ``day``, save it and re-render. Returns the exit code.

    The generated files show the latest day in the history: ``day`` itself, unless
    an earlier day was recorded with ``--date``.
    """

    if report.missing:
        console.warning(
            f"Spotify does not know {len(report.missing)} artist id(s); remove them from artists.txt: "
            + ", ".join(report.missing)
        )
    if report.stopped:
        console.warning(f"Stopped early: {report.stopped}.")
    if not report.stats:
        console.error(f"No artist stats were fetched with {report.requests} request(s), so nothing was written.")
        return 1
    new_rows = rows_for_day(day, (report.stats[i] for i in artist_ids if i in report.stats))
    rows = upsert(history, new_rows)
    changed = ["data/history.csv"] if write_history(paths.history, rows) else []
    shown = latest_day(rows, artist_ids) or day
    cities = _stored_cities(paths, shown)
    if shown == day:
        cities.update({stats.id: stats.top_cities for stats in report.stats.values()})
    changed += render_outputs(paths, rows, artist_ids, shown, cities, top=top)
    unknown = set(report.missing)
    failed = [artist_id for artist_id in report.failed if artist_id not in unknown]
    console.info(
        f"Recorded {len(new_rows)} of {len(artist_ids)} artists for {day} with {report.requests} request(s). "
        + (f"The files show {shown}, the latest day in data/history.csv. " if shown != day else "")
        + (f"Updated: {', '.join(changed)}." if changed else "No file changed.")
    )
    if failed:
        shown = ", ".join(failed[:20]) + (" ..." if len(failed) > 20 else "")
        console.warning(f"{len(failed)} artist(s) got no stats this run; the next run will try again: {shown}")
    return 0


def run_tracker(
    paths: Paths,
    artist_ids: Sequence[str],
    day: str,
    fetch_batch: FetchBatch,
    console: Console,
    *,
    history: Sequence[Row],
    budget: Optional[RequestBudget] = None,
    retry_passes: int = 2,
    time_limit: Optional[float] = None,
    pause: float = 1.0,
    top: int = 100,
) -> int:
    """Fetch, record and render with any ``fetch_batch`` (the SDK one in ``run``).

    ``history`` is data/history.csv as read before fetching, so that a broken file
    stops the run before it spends any request. ``budget`` caps the requests and
    ``time_limit`` (seconds) the time spent fetching.
    """

    report = fetch_stats(
        artist_ids,
        fetch_batch,
        retry_passes=retry_passes,
        budget=budget,
        time_limit=time_limit,
        pause=pause,
        log=console.info,
    )
    return record_and_render(paths, artist_ids, day, report, console, history=history, top=top)


def _max_requests(value: Optional[int], env: Mapping[str, str]) -> int:
    if value is not None:
        result = value
    else:
        text = (env.get("TRACKER_MAX_REQUESTS") or "").strip()
        if not text:
            return DEFAULT_MAX_REQUESTS
        try:
            result = int(text)
        except ValueError:
            raise ValueError(f"TRACKER_MAX_REQUESTS must be a whole number, not {text!r}") from None
    if result < 1:
        raise ValueError("the request limit must be at least 1")
    return result


def _day(value: Optional[str]) -> str:
    if not value:
        return datetime.now(timezone.utc).date().isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"--date must be YYYY-MM-DD, not {value!r}") from None


def _positive(option: str, value: float) -> float:
    if not value > 0:  # also refuses NaN
        raise ValueError(f"{option} must be greater than 0")
    return value


def cmd_run(args: argparse.Namespace, env: Mapping[str, str], session: Any, console: Console) -> int:
    paths = Paths.at(Path(args.root), Path(args.artists) if args.artists else None)
    artists = load_artists(paths, console)
    if artists is None:
        return 1
    if not artists.ids:
        console.error(f"{paths.artists.name} lists no artists.")
        return 1
    key = (env.get("SPOTIFY_API_KEY") or "").strip()
    if not key:
        console.notice(
            "SPOTIFY_API_KEY is not set, so there is nothing to fetch. Add your API key as a repository "
            "secret named SPOTIFY_API_KEY (Settings > Secrets and variables > Actions) to start tracking."
        )
        return 0
    base = env.get("SPOTIFY_API_BASE")
    try:
        provider, _, _ = resolve_api(base)
        max_requests = _max_requests(args.max_requests, env)
        day = _day(args.date)
        timeout = _positive("--timeout", args.timeout)
        time_limit = _positive("--time-limit", args.time_limit) * 60
        # Before the client exists: a broken history file must not cost a single request.
        history = read_history(paths.history)
    except ValueError as error:
        console.error(str(error))
        return 1
    count = len(artists.ids)
    needed = requests_needed(count)
    console.info(
        f"{count} artists: {needed} request(s) of up to {BATCH_SIZE} through "
        f"{'RapidAPI' if provider == 'rapidapi' else 'the direct API'}, at most {max_requests} this run "
        f"(retries included). Recording {day} (UTC)."
    )
    if needed > max_requests:
        console.warning(
            f"{count} artists need {needed} requests but this run may send {max_requests}: the last "
            f"{count - max_requests * BATCH_SIZE} artists in the list are skipped. Raise TRACKER_MAX_REQUESTS "
            "if your plan allows more."
        )
    budget = RequestBudget(max_requests)
    client = make_client(key, base, timeout=timeout, retries=args.retries, budget=budget, session=session)
    try:
        return run_tracker(
            paths,
            artists.ids,
            day,
            sdk_fetcher(client),
            console,
            history=history,
            budget=budget,
            retry_passes=args.retry_passes,
            time_limit=time_limit,
            pause=args.pause,
            top=args.top,
        )
    finally:
        client.close()


def cmd_render(args: argparse.Namespace, console: Console) -> int:
    paths = Paths.at(Path(args.root), Path(args.artists) if args.artists else None)
    artists = load_artists(paths, console)
    if artists is None:
        return 1
    try:
        rows = read_history(paths.history)
        latest = latest_day(rows, artists.ids)
        day = _day(args.date) if args.date else latest
    except ValueError as error:
        console.error(str(error))
        return 1
    if latest is None or day is None:
        console.info("data/history.csv has no rows for these artists yet; nothing to render.")
        return 0
    if not entries_for_day(rows, day, artists.ids):
        console.error(f"data/history.csv has no rows for {day}.")
        return 1
    changed = render_outputs(paths, rows, artists.ids, day, _stored_cities(paths, day), top=args.top)
    console.info(
        f"Rendered {day}. "
        + (f"Updated: {', '.join(changed)}." if changed else "No file changed.")
        + (f" data/latest.json stays on {latest}, the latest day." if day != latest else "")
    )
    return 0


def cmd_check(args: argparse.Namespace, console: Console) -> int:
    paths = Paths.at(Path(args.root), Path(args.artists) if args.artists else None)
    artists = load_artists(paths, console)
    if artists is None:
        return 1
    count = len(artists.ids)
    needed = requests_needed(count)
    console.info(
        f"{paths.artists.name}: {count} artists, {needed} request(s) per daily run "
        f"(up to {BATCH_SIZE} artists each), about {needed * 31} a month before retries."
    )
    if needed > DEFAULT_MAX_REQUESTS:
        console.warning(
            f"That is more than the default limit of {DEFAULT_MAX_REQUESTS} requests per run; "
            "set TRACKER_MAX_REQUESTS if your plan allows it."
        )
    if artists.errors:
        console.error(f"{len(artists.errors)} line(s) of {paths.artists.name} are not Spotify artists.")
        return 1
    if not count:
        console.error(f"{paths.artists.name} lists no artists.")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".", help="repository folder (default: current folder)")
    common.add_argument("--artists", help="artist list file (default: ROOT/artists.txt)")
    common.add_argument("--top", type=int, default=100, help="rows in LEADERBOARD.md (default: 100)")

    parser = argparse.ArgumentParser(
        prog="python -m tracker",
        description="Daily Spotify monthly listeners tracker.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="{run,render,check}")

    run = commands.add_parser("run", parents=[common], help="fetch today's numbers and update the files")
    run.add_argument("--date", help="UTC day to record, YYYY-MM-DD (default: today)")
    run.add_argument(
        "--max-requests",
        type=int,
        help=f"requests this run may send, retries included (default: TRACKER_MAX_REQUESTS or {DEFAULT_MAX_REQUESTS})",
    )
    run.add_argument(
        "--retry-passes",
        type=int,
        default=2,
        help="extra passes for artists whose stats were not ready or whose request failed (default: 2)",
    )
    run.add_argument(
        "--retries",
        type=int,
        default=0,
        help="SDK retries of a request that failed with 429/5xx or a network error, each counted towards "
        "--max-requests (default: 0; the next pass asks for those artists again)",
    )
    run.add_argument("--timeout", type=float, default=90.0, help="seconds to wait for one response (default: 90)")
    run.add_argument(
        "--time-limit",
        type=float,
        default=DEFAULT_TIME_LIMIT,
        help=f"minutes after which no new request is started (default: {DEFAULT_TIME_LIMIT:g})",
    )
    run.add_argument("--pause", type=float, default=1.0, help="seconds between requests (default: 1)")

    render = commands.add_parser("render", parents=[common], help="rewrite the generated files from data/history.csv")
    render.add_argument("--date", help="day to render, YYYY-MM-DD (default: the latest in the history)")

    commands.add_parser("check", parents=[common], help="validate artists.txt")
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
    session: Any = None,
    write: Optional[Callable[[str], None]] = None,
) -> int:
    """Entry point. ``env``, ``session`` (a requests-like session) and ``write`` are for tests."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0].startswith("-") and arguments[0] not in ("-h", "--help", "--version"):
        arguments.insert(0, "run")
    args = build_parser().parse_args(arguments)
    env = os.environ if env is None else env
    console = Console(write or print, github=env.get("GITHUB_ACTIONS") == "true")
    if args.command == "render":
        return cmd_render(args, console)
    if args.command == "check":
        return cmd_check(args, console)
    return cmd_run(args, env, session, console)
