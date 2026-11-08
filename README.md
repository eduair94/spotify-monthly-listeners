# Spotify monthly listeners tracker

[![Daily update](https://github.com/eduair94/spotify-monthly-listeners/actions/workflows/daily.yml/badge.svg)](https://github.com/eduair94/spotify-monthly-listeners/actions/workflows/daily.yml)
[![Tests](https://github.com/eduair94/spotify-monthly-listeners/actions/workflows/ci.yml/badge.svg)](https://github.com/eduair94/spotify-monthly-listeners/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A daily record of Spotify **monthly listeners**, **followers** and **world rank** for 500
artists, kept as plain CSV and JSON in this repository. It is also a small tracker you can fork
to record the same history for your own list of artists.

Unofficial: not affiliated with, endorsed by or sponsored by Spotify.

## Top 10 today

<!-- leaderboard:start -->
Top 10 of the tracked artists on **2026-11-08** (UTC). Full top 100 with 1-day changes: [LEADERBOARD.md](LEADERBOARD.md).

| # | Artist | Monthly listeners | 7-day change |
|--:|:--|--:|--:|
| 1 | [Bruno Mars](https://open.spotify.com/artist/0du5cEVh5yTK9QJze8zA0C) | 125,480,040 | -1,419,458 (-1.12%) |
| 2 | [Rihanna](https://open.spotify.com/artist/5pKCCKE2ajJHZ9KAiaK11H) | 117,315,212 | -87,156 (-0.07%) |
| 3 | [The Weeknd](https://open.spotify.com/artist/1Xyo4u8uXC1ZmMpatF05PJ) | 117,075,661 | +618,364 (+0.53%) |
| 4 | [Justin Bieber](https://open.spotify.com/artist/1uNFoZAHBGtllmzznpCI3s) | 110,855,999 | -41,693 (-0.04%) |
| 5 | [Lady Gaga](https://open.spotify.com/artist/1HY2Jd0NmPuamShAr6KMms) | 103,779,316 | +1,850,641 (+1.82%) |
| 6 | [Taylor Swift](https://open.spotify.com/artist/06HL4z0CvFAxyc27GXpf02) | 98,169,810 | -2,218,923 (-2.21%) |
| 7 | [Drake](https://open.spotify.com/artist/3TVXtAsR1Inumwj472S9r4) | 93,787,792 | -931,607 (-0.98%) |
| 8 | [Ariana Grande](https://open.spotify.com/artist/66CXWjxzNUsdJxJ2JdwvnR) | 92,927,864 | +499,973 (+0.54%) |
| 9 | [Michael Jackson](https://open.spotify.com/artist/3fMbdgg4jU18AjLCKBhRSm) | 91,753,327 | +2,996,124 (+3.38%) |
| 10 | [Coldplay](https://open.spotify.com/artist/4gzpq5DPGxSnKTe4SA8HAU) | 90,362,314 | -1,161,184 (-1.27%) |
<!-- leaderboard:end -->

## What is in this repository

| Path | What it is |
| --- | --- |
| [`data/history.csv`](data/history.csv) | One row per artist per day (UTC) since tracking started. New days are appended. |
| [`data/latest.json`](data/latest.json) | The latest day for every tracked artist, with 1-day and 7-day changes and top cities. |
| [`LEADERBOARD.md`](LEADERBOARD.md) | Top 100 by monthly listeners with changes, plus the biggest gains and drops. |
| [`artists.txt`](artists.txt) | The tracked artists. Edit it in your fork. |
| [`tracker/`](tracker) | The Python code: fetch, merge into the history, render. |
| [`.github/workflows/daily.yml`](.github/workflows/daily.yml) | Runs the tracker once a day and commits the result. |

Once a day (scheduled for 07:17 UTC; GitHub sometimes starts it later) a GitHub Actions workflow
asks the API for the current numbers of every artist in `artists.txt`, adds one row per artist
to `data/history.csv` and regenerates the other files.

## Using the data

The files have stable URLs, so you can read them straight from GitHub:

```python
import pandas as pd

url = "https://raw.githubusercontent.com/eduair94/spotify-monthly-listeners/main/data/history.csv"
history = pd.read_csv(url, parse_dates=["date"])

# Monthly listeners of one artist over time
weeknd = history[history.artist_id == "1Xyo4u8uXC1ZmMpatF05PJ"].set_index("date")["monthly_listeners"]
```

```bash
curl -s https://raw.githubusercontent.com/eduair94/spotify-monthly-listeners/main/data/latest.json \
  | jq '.artists[:5][] | {name, monthly_listeners, change_7d}'
```

### Columns of history.csv

| Column | Meaning |
| --- | --- |
| `date` | The UTC day the numbers were read (`YYYY-MM-DD`). |
| `artist_id` | Spotify artist id: the artist page is `https://open.spotify.com/artist/<artist_id>`. |
| `name` | The artist's name on that day. |
| `monthly_listeners` | Unique listeners in the last 28 days, as shown on the artist's Spotify page. Empty when Spotify shows no count (no listeners in 28 days). |
| `followers` | Spotify followers. |
| `world_rank` | Spotify's own global rank, which Spotify shows only for its most-listened artists. Empty for the others. |

Good to know:

- Monthly listeners is a rolling 28-day count that Spotify updates about once a day, so a
  day-to-day change also reflects listening from four weeks earlier leaving the window.
- If an artist has no row for a day, the API could not read that artist's numbers in that run.
  Nothing is estimated or carried forward.
- The history keeps what Spotify showed at the time of each run, even if Spotify later revises it.

## Track your own artists

1. **Fork** this repository.
2. **Edit `artists.txt`**: one artist per line, as a Spotify artist id, a `spotify:artist:` URI or
   an `open.spotify.com/artist/` link (`#` starts a comment). To start your own history from
   scratch, also delete `data/history.csv` and `data/latest.json`.
3. **Get an API key** (see below).
4. **Add the key as a repository secret**: Settings → Secrets and variables → Actions → New
   repository secret, named `SPOTIFY_API_KEY`. With a direct key (not RapidAPI), also add a secret
   `SPOTIFY_API_BASE` with the value `https://spotify-proxy.checkleaked.cc/spotify-data`.
5. **Enable the workflows**: GitHub turns scheduled workflows off in new forks. Open the Actions
   tab, enable them, then start **Daily update** once by hand with "Run workflow".

The workflow then commits new numbers once a day. Without the secret it skips the update and says
why in its log, so a fork without a key does not fail.

Optional: a repository *variable* `TRACKER_MAX_REQUESTS` (Settings → Secrets and variables →
Actions → Variables) sets the most requests one run may send, retries included. The default is 30.

### Getting an API key

The tracker uses the [Spotify Data API](https://spotify.checkleaked.cc), which reads the numbers
from public artist pages. You need a key from one of these:

- **RapidAPI** ([spotify81 on RapidAPI](https://rapidapi.com/airaudoeduardo/api/spotify81)). The
  free Basic plan allows 1,000 requests a month, as a hard limit: requests past it are refused,
  not charged. RapidAPI asks for a payment card even to subscribe to the free plan, because the
  API also has paid plans ([RapidAPI's pricing rules](https://docs.rapidapi.com/docs/api-pricing)).
- **A direct key** from [spotify.checkleaked.cc/pricing](https://spotify.checkleaked.cc/pricing).
  Set `SPOTIFY_API_BASE` as described above.

One request returns up to 50 artists. A run sends at most 30 requests by default, retries
included, so one run a day stays at or under 930 requests in a 31-day month: inside the free
plan, with room for an occasional run started by hand. 30 requests cover 1,500 artists when every
answer is complete; keep your list to about 1,400 artists so that each run has two requests left
for asking again about artists whose numbers were not ready.

| Artists in `artists.txt` | Requests a day | Requests a month (31 days) |
| --: | --: | --: |
| 50 | 1 | 31 |
| 500 (this list) | 10 | 310 |
| 1,000 | 20 | 620 |
| 1,400 | 28 | 868 |
| 1,500 | 30 | 930 |

The table counts one request per 50 artists. Asking again takes one more request per 50 artists
whose numbers were not ready. `python -m tracker check` prints the count for your list.

## Run it on your computer

```bash
python -m pip install -r requirements.txt
export SPOTIFY_API_KEY=your-key          # PowerShell: $env:SPOTIFY_API_KEY = "your-key"
python -m tracker run                    # fetch today's numbers and update the files
python -m tracker render                 # rebuild the generated files from data/history.csv only
python -m tracker check                  # validate artists.txt

python -m pip install -r requirements-dev.txt
python -m pytest                         # offline tests, no key needed
```

`python -m tracker run --help` lists the options (request limit, retry passes, SDK retries,
timeouts, date). Python 3.9 or newer.

## How it works

For each group of 50 artists the tracker sends one request, `GET /artists?ids=<ids>&include=stats`,
through the [checkleaked-spotify-api](https://pypi.org/project/checkleaked-spotify-api/) Python
SDK. The answer has each artist's followers, monthly listeners, world rank and top cities.
Artists whose numbers were not ready in time, and those of a request that failed, are asked for
again in up to two more passes. If a whole pass gets no numbers at all, for example while the API
is down, the next pass sends one request and goes on only if that one gets numbers, so an outage
costs one request per 50 artists, plus one. Every request counts towards the run's limit, and no
new request starts after 20 minutes. Ids that Spotify does not know are reported so you can
remove them. The day's rows are then merged into `data/history.csv`: running again on the same
day replaces that day's rows instead of adding duplicates. Finally `LEADERBOARD.md`,
`data/latest.json` and the table at the top of this file are regenerated.

You can get the same numbers for any artist from the API directly. The
[Spotify monthly listeners API guide](https://spotify.checkleaked.cc/spotify-monthly-listeners-api)
has the details; with the SDK:

```python
from spotify_api_sdk import SpotifyApiClient

client = SpotifyApiClient(api_key="your-rapidapi-key")
body = client.artists.get(["06HL4z0CvFAxyc27GXpf02", "1Xyo4u8uXC1ZmMpatF05PJ"], include=["stats"])
for artist in body["artists"]:
    # stats is null when the numbers were not ready in time
    print(artist["name"], (artist.get("stats") or {}).get("monthly_listeners"))
```

## How the 500 artists were picked

They come from 67 weekly Spotify Top 200 charts read through the API: the week of 2026-10-29
for the global chart and 34 countries (United States, Canada, Mexico, Brazil, Argentina,
Colombia, Chile, Peru, Dominican Republic, United Kingdom, Germany, France, Spain, Italy,
Netherlands, Sweden, Poland, Portugal, Turkey, Japan, South Korea, India, Indonesia, Philippines,
Thailand, Vietnam, Taiwan, Malaysia, South Africa, Nigeria, Egypt, Morocco, Saudi Arabia and
Australia), plus four earlier weeks between November 2025 and August 2026 for the global, US, UK,
Brazil, Mexico, India, Japan and South Korea charts.

Each chart's six strongest artists (the most and highest-ranked tracks) are in; the rest are the
artists that appear on the most charts. Chart credits that are not performers (video games, a
film soundtrack, record labels) were left out, as was one profile with almost no listeners of its
own; the next artists in line took their places. The list therefore leans towards artists with
recent hits, and well-known artists without a recent chart entry can be missing. The leaderboard
ranks the tracked artists only; it is not a ranking of all artists on Spotify.

## Links

- [Website and live demo](https://spotify.checkleaked.cc)
- [Spotify monthly listeners API guide](https://spotify.checkleaked.cc/spotify-monthly-listeners-api)
- [Pricing for direct keys](https://spotify.checkleaked.cc/pricing)
- [spotify81 on RapidAPI](https://rapidapi.com/airaudoeduardo/api/spotify81)
- [Python SDK on PyPI](https://pypi.org/project/checkleaked-spotify-api/): `pip install checkleaked-spotify-api`

## License and data

The code is MIT licensed; see [LICENSE](LICENSE). The data comes from public Spotify artist
pages, collected through the API, and is provided as is, without any guarantee of accuracy or
completeness; see [DATA_NOTICE.md](DATA_NOTICE.md). This project is unofficial and is not
affiliated with, endorsed by or sponsored by Spotify. Spotify is a trademark of Spotify AB.
