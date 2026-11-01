"""Offline test helpers: a fake API that answers like GET /artists?include=stats."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

TAYLOR = "06HL4z0CvFAxyc27GXpf02"
WEEKND = "1Xyo4u8uXC1ZmMpatF05PJ"
BUNNY = "4q3ewBCX7sLwd24euuV69X"
BRUNO = "0du5cEVh5yTK9QJze8zA0C"
UNKNOWN = "0000000000000000000000"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fake_id(number: int) -> str:
    """A syntactically valid, made-up artist id."""

    return f"fake{number:018d}"


def artist_object(
    artist_id: str, name: str, listeners: Optional[int], followers: Optional[int] = None, rank: Optional[int] = None
) -> Dict[str, Any]:
    return {
        "id": artist_id,
        "name": name,
        "type": "artist",
        "followers": {"href": None, "total": followers},
        "stats": {
            "followers": followers,
            "monthly_listeners": listeners,
            "world_rank": rank or 0,
            "top_cities": [{"city": "London", "country": "GB", "region": "ENG", "listeners": 1000}],
        },
    }


class FakeApi:
    """Answers GET /artists for a set of known artists.

    ``slow`` ids come back with ``stats: null`` the first time they are asked for,
    ``never_ready`` ids every time, and ``missing`` ids are reported as unknown to Spotify.
    """

    def __init__(
        self,
        artists: Iterable[Dict[str, Any]],
        *,
        slow: Iterable[str] = (),
        never_ready: Iterable[str] = (),
        missing: Iterable[str] = (),
    ) -> None:
        self.artists = {artist["id"]: artist for artist in artists}
        self.slow = set(slow)
        self.never_ready = set(never_ready)
        self.missing = set(missing)
        self.calls: List[List[str]] = []

    def answer(self, ids: List[str]) -> Dict[str, Any]:
        self.calls.append(list(ids))
        out = []
        for artist_id in ids:
            if artist_id in self.missing or artist_id not in self.artists:
                continue
            artist = json.loads(json.dumps(self.artists[artist_id]))
            if artist_id in self.slow or artist_id in self.never_ready:
                self.slow.discard(artist_id)
                artist["stats"] = None
            out.append(artist)
        resolved = sum(1 for artist in out if artist["stats"])
        return {
            "artists": out,
            "missing": [i for i in ids if i in self.missing],
            "unavailable": [],
            "include_status": {"stats": {"requested": len(out), "resolved": resolved}},
        }

    def __call__(self, ids: List[str]) -> Dict[str, Any]:
        return self.answer(ids)


class FakeResponse:
    def __init__(self, status: int, body: Any, headers: Optional[Dict[str, str]] = None) -> None:
        self.status_code = status
        self.headers = {"content-type": "application/json", **(headers or {})}
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")


class FakeSession:
    """Stands in for requests.Session inside the SDK client; records every request."""

    def __init__(self, api: Optional[FakeApi] = None, *, status: int = 200, error_body: Any = None) -> None:
        self.api = api
        self.status = status
        self.error_body = error_body
        self.requests: List[Dict[str, Any]] = []
        self.closed = False

    def request(self, method: str, url: str, headers: Any = None, data: Any = None, timeout: Any = None) -> FakeResponse:
        self.requests.append({"method": method, "url": url, "headers": dict(headers or {}), "timeout": timeout})
        if self.status != 200:
            return FakeResponse(self.status, self.error_body or {"message": "error"})
        query = parse_qs(urlsplit(url).query)
        ids = query.get("ids", [""])[0].split(",")
        assert self.api is not None
        return FakeResponse(200, self.api.answer(ids))

    def close(self) -> None:
        self.closed = True
