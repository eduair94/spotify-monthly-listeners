"""Fetch artist stats: GET /artists?ids=<up to 50 ids>&include=stats.

One request covers up to 50 artists. The API fills ``stats`` within its own time
budget, so on a busy day a few artists come back with ``stats: null``: they are
asked for again, together with the artists of failed requests, in up to
``retry_passes`` extra passes. After a pass that got no stats at all, the next
pass goes on only if its first request gets some, so an outage costs one request
per 50 artists plus one. Artists the API lists as ``missing`` are unknown to
Spotify and are not retried.

Every HTTP request counts towards the run's :class:`RequestBudget`, the SDK's
automatic retries included, so the limit holds on a quota with a hard cap.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

#: The most artist ids GET /artists accepts per request.
BATCH_SIZE = 50
RAPIDAPI_BASE = "https://spotify81.p.rapidapi.com"
DIRECT_BASE = "https://spotify-proxy.checkleaked.cc/spotify-data"
USER_AGENT = "spotify-monthly-listeners-tracker/1.0 (+https://github.com/eduair94/spotify-monthly-listeners)"

FetchBatch = Callable[[List[str]], Any]
Log = Callable[[str], None]


@dataclass
class ArtistStats:
    """One artist's numbers as the API returned them (None = not shown by Spotify)."""

    id: str
    name: str
    monthly_listeners: Optional[int]
    followers: Optional[int]
    world_rank: Optional[int]
    top_cities: List[Dict[str, Any]] = field(default_factory=list)


class FatalFetchError(Exception):
    """Stop the run: asking again will not help (key refused, quota used up)."""


class RequestLimitReached(FatalFetchError):
    """The run has sent as many requests as it may."""


class BatchRejected(Exception):
    """The API refused this request (a 4xx other than 408/429): do not resend these ids."""


class RequestBudget:
    """The HTTP requests one run may send, counted as they go out.

    :func:`fetch_stats` spends one per batch. :func:`make_client` installs
    :meth:`spend` as the SDK client's ``on_retry`` hook, so each automatic retry
    is counted too, and a retry past the limit is never sent: the hook raises
    :class:`RequestLimitReached` instead.
    """

    def __init__(self, limit: Optional[int] = None) -> None:
        if limit is not None and limit < 1:
            raise ValueError("the request limit must be at least 1")
        self.limit = limit
        self.sent = 0

    @property
    def used_up(self) -> bool:
        return self.limit is not None and self.sent >= self.limit

    def spend(self, _info: Any = None) -> None:
        """Count one request about to be sent, or raise :class:`RequestLimitReached`."""

        if self.used_up:
            raise RequestLimitReached(f"the limit of {self.limit} requests per run was reached")
        self.sent += 1


def batched(items: Sequence[str], size: int = BATCH_SIZE) -> List[List[str]]:
    """Split ``items`` into lists of at most ``size``, keeping the order."""

    if size < 1:
        raise ValueError("size must be at least 1")
    return [list(items[start : start + size]) for start in range(0, len(items), size)]


def requests_needed(count: int, size: int = BATCH_SIZE) -> int:
    """How many requests ``count`` artists take when nothing has to be retried."""

    return -(-count // size) if count > 0 else 0


def _count(value: Any) -> Optional[int]:
    """A non-negative whole number from a JSON value, else None."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _cities(value: Any) -> List[Dict[str, Any]]:
    out = []
    for city in value if isinstance(value, list) else []:
        if not isinstance(city, dict):
            continue
        region = city.get("region")
        out.append(
            {
                "city": str(city.get("city") or ""),
                "country": str(city.get("country") or ""),
                "region": str(region) if region else None,
                "listeners": _count(city.get("listeners")),
            }
        )
    return out


def _id_list(value: Any) -> List[str]:
    ids = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            item = item.get("id")
        if isinstance(item, str) and item:
            ids.append(item)
    return ids


@dataclass
class BatchResult:
    stats: Dict[str, ArtistStats]
    #: Requested ids without stats this time (stats not ready, or "unavailable"): ask again.
    retry: List[str]
    #: Requested ids Spotify does not know: remove them from artists.txt.
    missing: List[str]


def parse_artists_response(body: Any, requested: Sequence[str]) -> BatchResult:
    """Read one GET /artists?include=stats response.

    An artist with a ``stats`` object is recorded even when some of its values are
    null (``monthly_listeners`` is null when Spotify shows none). An artist whose
    ``stats`` is null, or that is not in the response at all, is retried.
    """

    if not isinstance(body, dict) or not isinstance(body.get("artists"), list):
        raise ValueError("unexpected response: no 'artists' list")
    wanted = set(requested)
    stats: Dict[str, ArtistStats] = {}
    for artist in body["artists"]:
        if not isinstance(artist, dict):
            continue
        artist_id = artist.get("id")
        values = artist.get("stats")
        if artist_id not in wanted or not isinstance(values, dict):
            continue
        followers = _count(values.get("followers"))
        if followers is None and isinstance(artist.get("followers"), dict):
            followers = _count(artist["followers"].get("total"))
        stats[artist_id] = ArtistStats(
            id=artist_id,
            name=str(artist.get("name") or "").strip(),
            monthly_listeners=_count(values.get("monthly_listeners")),
            followers=followers,
            # Spotify ranks only its top artists; 0 means "not ranked".
            world_rank=_count(values.get("world_rank")) or None,
            top_cities=_cities(values.get("top_cities")),
        )
    missing = [i for i in dict.fromkeys(_id_list(body.get("missing"))) if i in wanted and i not in stats]
    retry = [i for i in dict.fromkeys(requested) if i not in stats and i not in missing]
    return BatchResult(stats=stats, retry=retry, missing=missing)


@dataclass
class FetchReport:
    stats: Dict[str, ArtistStats] = field(default_factory=dict)
    #: Ids Spotify does not know.
    missing: List[str] = field(default_factory=list)
    #: Ids still without stats after every pass (or skipped when the run stopped early).
    failed: List[str] = field(default_factory=list)
    #: HTTP requests sent, the SDK's automatic retries included.
    requests: int = 0
    #: Why the run stopped before asking for every artist, if it did.
    stopped: Optional[str] = None


def _describe(error: BaseException) -> str:
    status = getattr(error, "status", None)
    code = getattr(error, "code", None)
    parts = [f"HTTP {status}" if status else type(error).__name__]
    if code and str(code) != str(status):
        parts.append(str(code))
    message = str(error).strip().splitlines()[0][:200] if str(error).strip() else ""
    return " ".join(parts) + (f": {message}" if message else "")


def _minutes(seconds: float) -> str:
    minutes = seconds / 60
    return f"{minutes:g} minute{'' if minutes == 1 else 's'}"


def _label(number: int, size: int, retries: int) -> str:
    """``Request 3 (50 artists)``, plus the SDK's automatic retries of that request."""

    extra = f", {retries} automatic retr{'y' if retries == 1 else 'ies'}" if retries else ""
    return f"Request {number} ({size} artists{extra})"


def fetch_stats(
    ids: Iterable[str],
    fetch_batch: FetchBatch,
    *,
    batch_size: int = BATCH_SIZE,
    retry_passes: int = 2,
    budget: Optional[RequestBudget] = None,
    time_limit: Optional[float] = None,
    pause: float = 1.0,
    log: Log = print,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> FetchReport:
    """Fetch stats for ``ids``, ``batch_size`` per request, retrying the ones that lack them.

    ``fetch_batch(ids)`` returns the decoded response body. It raises
    :class:`FatalFetchError` to stop the run, :class:`BatchRejected` to drop a
    batch, and anything else for a failure worth retrying in the next pass.
    ``budget`` caps the requests of the whole run, retry passes included (no cap
    without one). No request is started once ``time_limit`` seconds have passed.
    After a pass without any stats, the next pass stops at its first request
    that gets none either.
    """

    budget = RequestBudget() if budget is None else budget
    started = clock()
    report = FetchReport()
    pending = list(dict.fromkeys(ids))
    rejected: List[str] = []
    dry = False  # the last pass got no stats at all
    given_up = False
    for pass_number in range(max(0, retry_passes) + 1):
        if not pending or report.stopped or given_up:
            break
        if pass_number:
            log(
                f"Retry pass {pass_number}: {len(pending)} artist(s) still without stats"
                + (". The last pass got none, so this one goes on only if its first request gets some." if dry else "")
            )
        stats_before = len(report.stats)
        batches = batched(pending, batch_size)
        pending = []
        for index, batch in enumerate(batches):
            if budget.used_up:
                report.stopped = f"the limit of {budget.limit} requests per run was reached"
            elif time_limit is not None and clock() - started >= time_limit:
                report.stopped = f"the time limit of {_minutes(time_limit)} per run was reached"
            if report.stopped:
                for rest in batches[index:]:
                    pending.extend(rest)
                break
            if budget.sent and pause > 0:
                sleep(pause)
            budget.spend()
            number = budget.sent
            stats_so_far = len(report.stats)
            try:
                result = parse_artists_response(fetch_batch(batch), batch)
            except FatalFetchError as error:  # also RequestLimitReached from an SDK retry
                report.stopped = outcome = str(error)
                for rest in batches[index:]:
                    pending.extend(rest)
            except BatchRejected as error:
                outcome = f"refused ({error}); these artists are skipped this run"
                rejected.extend(batch)
            except Exception as error:  # network trouble, 5xx, odd body
                outcome = f"failed ({_describe(error)})"
                pending.extend(batch)
            else:
                report.stats.update(result.stats)
                report.missing.extend(result.missing)
                pending.extend(result.retry)
                notes = [f"stats for {len(result.stats)}"]
                if result.retry:
                    notes.append(f"{len(result.retry)} to ask again")
                if result.missing:
                    notes.append(f"{len(result.missing)} unknown to Spotify")
                outcome = ", ".join(notes)
            log(f"{_label(number, len(batch), budget.sent - number)}: {outcome}")
            if report.stopped:
                break
            if len(report.stats) > stats_so_far:
                dry = False
            elif dry:
                # Nothing in the whole last pass and nothing now (an outage, or stats that never
                # get ready): asking again would only spend requests.
                given_up = True
                for rest in batches[index + 1 :]:
                    pending.extend(rest)
                log(f"Still no stats, so the {len(pending)} artist(s) left are not asked for again this run.")
                break
        dry = len(report.stats) == stats_before
    report.requests = budget.sent
    report.failed = list(dict.fromkeys(pending + rejected))
    return report


# --- the API client -----------------------------------------------------------


def resolve_api(base_url: Optional[str]) -> Tuple[str, Optional[str], Optional[str]]:
    """``(provider, base_url, host)`` for the SDK client from SPOTIFY_API_BASE.

    Empty means RapidAPI (spotify81). A ``*.rapidapi.com`` URL is RapidAPI with
    that host header; any other URL is the direct API.
    """

    if base_url is None or not base_url.strip():
        return "rapidapi", None, None
    base = base_url.strip().rstrip("/")
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"SPOTIFY_API_BASE must be a full http(s) URL such as {DIRECT_BASE}")
    host = parts.hostname.lower()
    if host == "rapidapi.com" or host.endswith(".rapidapi.com"):
        return "rapidapi", base, host
    return "proxy", base, None


def make_client(
    api_key: str,
    base_url: Optional[str] = None,
    *,
    timeout: float = 90.0,
    retries: int = 0,
    budget: Optional[RequestBudget] = None,
    session: Any = None,
) -> Any:
    """A ``spotify_api_sdk.SpotifyApiClient`` for RapidAPI or the direct API.

    ``retries`` are the SDK's automatic retries of a request that failed with
    429/5xx or a network error. There are none by default: :func:`fetch_stats`
    asks for those artists again in its next pass. Each retry is spent from
    ``budget`` when one is given.
    """

    import spotify_api_sdk
    from spotify_api_sdk import SpotifyApiClient

    provider, base, host = resolve_api(base_url)
    return SpotifyApiClient(
        api_key,
        provider=provider,
        base_url=base,
        host=host,
        timeout=timeout,
        retries=retries,
        on_retry=budget.spend if budget is not None else None,
        session=session,
        user_agent=f"{USER_AGENT} checkleaked-spotify-api-python/{spotify_api_sdk.__version__}",
    )


#: Error codes that mean the key itself cannot be used.
_KEY_CODES = frozenset({"MISSING_API_KEY", "INVALID_API_KEY", "KEY_REVOKED", "KEY_EXPIRED"})


def sdk_fetcher(client: Any) -> FetchBatch:
    """``fetch_batch`` for :func:`fetch_stats` backed by the SDK client (see :func:`make_client`)."""

    from spotify_api_sdk import AuthError, HttpError

    def fetch(ids: List[str]) -> Any:
        try:
            return client.artists.get(ids, include=["stats"])
        except HttpError as error:
            status = error.status or 0
            if isinstance(error, AuthError) or status == 401 or error.code in _KEY_CODES:
                raise FatalFetchError(
                    f"the API refused the key ({_describe(error)}). Check the SPOTIFY_API_KEY secret: "
                    "a RapidAPI key must be subscribed to the spotify81 API, and a direct key also "
                    f"needs SPOTIFY_API_BASE={DIRECT_BASE}"
                ) from None
            if status == 402 or error.code == "QUOTA_EXCEEDED" or (status == 429 and not error.retryable):
                raise FatalFetchError(f"the plan's request quota is used up ({_describe(error)})") from None
            if 400 <= status < 500 and status not in (408, 429):
                raise BatchRejected(_describe(error)) from None
            raise

    return fetch
