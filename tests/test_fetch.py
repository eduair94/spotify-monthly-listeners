from urllib.parse import parse_qs, urlsplit

import pytest
from helpers import BRUNO, BUNNY, TAYLOR, UNKNOWN, WEEKND, FakeApi, FakeSession, artist_object, fake_id, load_fixture

from tracker.fetch import (
    DIRECT_BASE,
    BatchRejected,
    FatalFetchError,
    RequestBudget,
    RequestLimitReached,
    batched,
    fetch_stats,
    make_client,
    parse_artists_response,
    requests_needed,
    resolve_api,
    sdk_fetcher,
)

# --- batching ----------------------------------------------------------------


def test_batches_of_fifty_keep_order():
    ids = [fake_id(i) for i in range(120)]
    batches = batched(ids)
    assert [len(batch) for batch in batches] == [50, 50, 20]
    assert [i for batch in batches for i in batch] == ids
    assert [len(batch) for batch in batched(ids[:50])] == [50]
    assert [len(batch) for batch in batched(ids[:51])] == [50, 1]
    assert batched([]) == []


def test_batch_size_must_be_positive():
    with pytest.raises(ValueError):
        batched(["a"], 0)


@pytest.mark.parametrize("count, requests", [(0, 0), (1, 1), (50, 1), (51, 2), (500, 10), (1650, 33)])
def test_requests_needed(count, requests):
    assert requests_needed(count) == requests


# --- one response --------------------------------------------------------------


def test_parses_the_api_response():
    requested = [TAYLOR, WEEKND, BUNNY, BRUNO, UNKNOWN]
    result = parse_artists_response(load_fixture("artists_stats.json"), requested)
    assert set(result.stats) == {TAYLOR, WEEKND, BUNNY}
    taylor = result.stats[TAYLOR]
    assert (taylor.name, taylor.monthly_listeners, taylor.followers, taylor.world_rank) == (
        "Taylor Swift",
        100388733,
        166079303,
        6,
    )
    assert taylor.top_cities[0] == {"city": "London", "country": "GB", "region": "ENG", "listeners": 1525725}
    assert result.retry == [BRUNO]  # stats: null, ask again
    assert result.missing == [UNKNOWN]  # unknown to Spotify, never retried


def test_value_rules():
    body = {
        "artists": [
            # world_rank 0 = not ranked; a null monthly_listeners is a value (no listeners shown), not a retry
            {"id": fake_id(1), "name": " Spaced ", "stats": {"followers": 10, "monthly_listeners": None, "world_rank": 0}},
            # followers missing from stats: use followers.total; numeric strings are accepted
            {"id": fake_id(2), "name": "B", "followers": {"total": 77}, "stats": {"monthly_listeners": "123", "world_rank": "4"}},
            # junk values become None
            {"id": fake_id(3), "name": "C", "stats": {"followers": -5, "monthly_listeners": True, "world_rank": 1.5}},
            # an artist nobody asked for is ignored
            {"id": fake_id(9), "name": "Z", "stats": {"monthly_listeners": 1}},
        ],
    }
    result = parse_artists_response(body, [fake_id(1), fake_id(2), fake_id(3), fake_id(4)])
    first, second, third = (result.stats[fake_id(n)] for n in (1, 2, 3))
    assert (first.name, first.monthly_listeners, first.followers, first.world_rank) == ("Spaced", None, 10, None)
    assert (second.monthly_listeners, second.followers, second.world_rank) == (123, 77, 4)
    assert (third.monthly_listeners, third.followers, third.world_rank) == (None, None, None)
    assert fake_id(9) not in result.stats
    assert result.retry == [fake_id(4)]  # asked for, not in the answer


def test_rejects_a_body_without_artists():
    with pytest.raises(ValueError):
        parse_artists_response({"error": "nope"}, [TAYLOR])
    with pytest.raises(ValueError):
        parse_artists_response(["not", "a", "dict"], [TAYLOR])


# --- the fetch loop -----------------------------------------------------------


def _universe(count):
    return [artist_object(fake_id(i), f"Artist {i}", 1000 + i) for i in range(count)]


def test_retries_only_the_artists_without_stats():
    ids = [fake_id(i) for i in range(120)]
    api = FakeApi(_universe(120), slow={ids[3], ids[60], ids[119]})
    report = fetch_stats(ids, api, pause=0, log=lambda _: None)
    assert set(report.stats) == set(ids)
    assert [len(call) for call in api.calls] == [50, 50, 20, 3]
    assert api.calls[-1] == [ids[3], ids[60], ids[119]]
    assert report.requests == 4 and report.failed == [] and report.stopped is None


def test_gives_up_after_the_retry_passes():
    ids = [fake_id(i) for i in range(3)]
    api = FakeApi(_universe(3), never_ready={ids[1]})
    report = fetch_stats(ids, api, retry_passes=2, pause=0, log=lambda _: None)
    assert report.failed == [ids[1]]
    assert len(api.calls) == 3  # first pass + 2 retry passes


def test_after_a_pass_without_stats_the_next_pass_checks_with_one_request():
    ids = [fake_id(i) for i in range(150)]
    api = FakeApi(_universe(150), never_ready=ids[50:])
    logged = []
    report = fetch_stats(ids, api, retry_passes=5, pause=0, log=logged.append)
    assert len(report.stats) == 50 and report.failed == ids[50:]
    # first pass: 3 requests; retry pass 1 gets nothing from 2; retry pass 2 stops after 1
    assert [len(call) for call in api.calls] == [50] * 6
    assert any(line.startswith("Retry pass 2: 100 artist(s) still without stats. The last pass got none") for line in logged)
    assert logged[-1] == "Still no stats, so the 100 artist(s) left are not asked for again this run."


def test_an_outage_costs_one_pass_and_one_more_request():
    ids = [fake_id(i) for i in range(500)]
    sent = []

    def fetch(batch):
        sent.append(batch)
        raise ConnectionError("connection refused")

    report = fetch_stats(ids, fetch, retry_passes=2, pause=0, log=lambda _: None)
    assert len(sent) == 11 and report.requests == 11  # 10 in the first pass, then 1 to check
    assert report.stats == {} and report.stopped is None
    assert report.failed == ids


def test_the_next_pass_goes_on_when_the_api_is_back():
    ids = [fake_id(i) for i in range(120)]
    api = FakeApi(_universe(120))
    sent = []

    def fetch(batch):
        sent.append(batch)
        if len(sent) <= 3:  # down for the whole first pass
            raise ConnectionError("connection refused")
        return api(batch)

    report = fetch_stats(ids, fetch, pause=0, log=lambda _: None)
    assert set(report.stats) == set(ids) and report.failed == []
    assert len(sent) == 6 and report.requests == 6


def test_unknown_artists_are_not_retried():
    ids = [fake_id(0), UNKNOWN]
    api = FakeApi(_universe(1), missing={UNKNOWN})
    report = fetch_stats(ids, api, pause=0, log=lambda _: None)
    assert report.missing == [UNKNOWN]
    assert len(api.calls) == 1
    assert report.failed == []


def test_stops_at_the_request_limit():
    ids = [fake_id(i) for i in range(120)]
    api = FakeApi(_universe(120))
    report = fetch_stats(ids, api, budget=RequestBudget(2), pause=0, log=lambda _: None)
    assert len(api.calls) == 2 and report.requests == 2
    assert len(report.stats) == 100
    assert report.failed == ids[100:]
    assert "limit of 2 requests" in report.stopped


def test_retry_passes_count_towards_the_limit():
    ids = [fake_id(i) for i in range(60)]
    api = FakeApi(_universe(60), slow={ids[0]})
    report = fetch_stats(ids, api, budget=RequestBudget(2), pause=0, log=lambda _: None)
    assert len(api.calls) == 2
    assert report.failed == [ids[0]]


def test_the_request_budget():
    with pytest.raises(ValueError):
        RequestBudget(0)
    budget = RequestBudget(2)
    budget.spend()
    budget.spend({"attempt": 1})  # also the SDK's on_retry hook
    assert budget.sent == 2 and budget.used_up
    with pytest.raises(RequestLimitReached, match="limit of 2 requests"):
        budget.spend()
    assert budget.sent == 2
    unlimited = RequestBudget()
    for _ in range(1000):
        unlimited.spend()
    assert not unlimited.used_up


def test_stops_starting_requests_at_the_time_limit():
    ids = [fake_id(i) for i in range(150)]
    api = FakeApi(_universe(150))
    now = [0.0]

    def fetch(batch):
        now[0] += 50  # every request takes 50 seconds
        return api(batch)

    report = fetch_stats(ids, fetch, time_limit=90, pause=0, log=lambda _: None, clock=lambda: now[0])
    assert len(api.calls) == 2  # started at 0 and 50 s; at 100 s the limit had passed
    assert len(report.stats) == 100 and report.failed == ids[100:]
    assert report.stopped == "the time limit of 1.5 minutes per run was reached"


def test_a_fatal_error_stops_and_keeps_what_was_fetched():
    ids = [fake_id(i) for i in range(120)]
    api = FakeApi(_universe(120))

    def fetch(batch):
        if len(api.calls) == 1:
            raise FatalFetchError("the plan's request quota is used up")
        return api(batch)

    report = fetch_stats(ids, fetch, pause=0, log=lambda _: None)
    assert len(report.stats) == 50
    assert report.stopped == "the plan's request quota is used up"
    assert report.failed == ids[50:]
    assert report.requests == 2


def test_a_failed_request_is_asked_again_in_the_next_pass():
    ids = [fake_id(i) for i in range(10)]
    api = FakeApi(_universe(10))
    failures = [ConnectionError("reset by peer")]

    def fetch(batch):
        if failures:
            raise failures.pop()
        return api(batch)

    logged = []
    report = fetch_stats(ids, fetch, pause=0, log=logged.append)
    assert set(report.stats) == set(ids)
    assert report.requests == 2
    assert logged[0] == "Request 1 (10 artists): failed (ConnectionError: reset by peer)"
    assert logged[-1] == "Request 2 (10 artists): stats for 10"


def test_a_refused_batch_is_not_sent_again():
    ids = [fake_id(i) for i in range(60)]
    api = FakeApi(_universe(60))

    def fetch(batch):
        if batch[0] == ids[0]:
            raise BatchRejected("HTTP 400")
        return api(batch)

    report = fetch_stats(ids, fetch, pause=0, log=lambda _: None)
    assert len(report.stats) == 10
    assert report.failed == ids[:50]
    assert report.requests == 2


def test_pauses_between_requests():
    ids = [fake_id(i) for i in range(120)]
    slept = []
    fetch_stats(ids, FakeApi(_universe(120)), pause=1.5, log=lambda _: None, sleep=slept.append)
    assert slept == [1.5, 1.5]


def test_duplicate_ids_are_asked_once():
    api = FakeApi(_universe(2))
    fetch_stats([fake_id(0), fake_id(1), fake_id(0)], api, pause=0, log=lambda _: None)
    assert api.calls == [[fake_id(0), fake_id(1)]]


# --- the SDK client -----------------------------------------------------------


def test_resolve_api():
    assert resolve_api(None) == ("rapidapi", None, None)
    assert resolve_api("  ") == ("rapidapi", None, None)
    assert resolve_api(DIRECT_BASE + "/") == ("proxy", DIRECT_BASE, None)
    assert resolve_api("https://spotify81.p.rapidapi.com") == (
        "rapidapi",
        "https://spotify81.p.rapidapi.com",
        "spotify81.p.rapidapi.com",
    )
    with pytest.raises(ValueError):
        resolve_api("spotify-proxy.checkleaked.cc")


def test_sdk_request_through_rapidapi():
    session = FakeSession(FakeApi([artist_object(TAYLOR, "Taylor Swift", 100), artist_object(WEEKND, "The Weeknd", 90)]))
    fetch = sdk_fetcher(make_client("test-key-123", None, session=session, retries=0))
    body = fetch([TAYLOR, WEEKND])
    assert [artist["id"] for artist in body["artists"]] == [TAYLOR, WEEKND]
    request = session.requests[0]
    url = urlsplit(request["url"])
    assert (url.scheme, url.netloc, url.path) == ("https", "spotify81.p.rapidapi.com", "/artists")
    assert parse_qs(url.query) == {"ids": [f"{TAYLOR},{WEEKND}"], "include": ["stats"]}
    assert request["headers"]["x-rapidapi-key"] == "test-key-123"
    assert request["headers"]["x-rapidapi-host"] == "spotify81.p.rapidapi.com"
    assert "spotify-monthly-listeners-tracker" in request["headers"]["user-agent"]


def test_sdk_request_through_the_direct_api():
    session = FakeSession(FakeApi([artist_object(TAYLOR, "Taylor Swift", 100)]))
    fetch = sdk_fetcher(make_client("direct-key", DIRECT_BASE, session=session, retries=0))
    fetch([TAYLOR])
    request = session.requests[0]
    assert request["url"].startswith(DIRECT_BASE + "/artists?")
    assert request["headers"]["x-rapidapi-key"] == "direct-key"
    assert "x-rapidapi-host" not in request["headers"]


@pytest.mark.parametrize(
    "status, body, expected",
    [
        (403, {"message": "You are not subscribed to this API."}, FatalFetchError),
        (401, {"message": "Invalid API key."}, FatalFetchError),
        (429, {"message": "You have exceeded the MONTHLY quota for Requests on your current plan, BASIC."}, FatalFetchError),
        (400, {"code": "INVALID_PARAMETER", "message": "ids", "param": "ids"}, BatchRejected),
    ],
)
def test_sdk_errors_are_classified(status, body, expected):
    fetch = sdk_fetcher(make_client("k", None, session=FakeSession(status=status, error_body=body), retries=0))
    with pytest.raises(expected):
        fetch([TAYLOR])


def test_a_server_error_is_left_for_the_retry_pass():
    from spotify_api_sdk import HttpError

    session = FakeSession(status=500, error_body={"message": "boom"})
    fetch = sdk_fetcher(make_client("k", None, session=session))
    with pytest.raises(HttpError) as raised:
        fetch([TAYLOR])
    assert not isinstance(raised.value, (FatalFetchError, BatchRejected))
    assert len(session.requests) == 1  # no automatic retries unless asked for


def test_sdk_retries_count_towards_the_request_limit(no_sdk_wait):
    ids = [fake_id(i) for i in range(120)]
    session = FakeSession(status=503, error_body={"message": "Service unavailable"})
    budget = RequestBudget(4)
    fetch = sdk_fetcher(make_client("k", None, session=session, retries=2, budget=budget))
    logged = []
    report = fetch_stats(ids, fetch, budget=budget, pause=0, log=logged.append)
    assert len(session.requests) == 4 and report.requests == 4 and budget.sent == 4
    assert report.stopped == "the limit of 4 requests per run was reached"
    assert report.failed == ids
    assert logged[0].startswith("Request 1 (50 artists, 2 automatic retries): failed (HTTP 503")
    # request 4 failed too, and its retry would have been the 5th request: it is not sent
    assert logged[1] == "Request 4 (50 artists): the limit of 4 requests per run was reached"
