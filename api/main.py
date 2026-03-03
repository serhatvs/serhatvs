import json
import os
from datetime import date, datetime, timedelta, timezone
from html import escape
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from fastapi import FastAPI, Response


app = FastAPI()

GITHUB_API_BASE = "https://api.github.com"
PALETTE = {
    "bg": "#2B0D3E",
    "card": "#2B2E33",
    "text": "#F5F6F7",
    "muted": "#C1C4C8",
    "royal": "#7A3F91",
    "gold": "#E6A520",
    "soft": "#C59DD9",
    "topaz": "#FFD77A",
}


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "serhatvs-profile-stats",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_get(path: str, params: dict[str, str | int] | None = None) -> tuple[dict | list, dict[str, str]]:
    url = f"{GITHUB_API_BASE}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"

    request = Request(url, headers=github_headers())
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
        response_headers = dict(response.info().items())
        return payload, response_headers


def parse_next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None

    for part in link_header.split(","):
        if 'rel="next"' not in part:
            continue
        start = part.find("<")
        end = part.find(">")
        if start != -1 and end != -1:
            return part[start + 1 : end]
    return None


def github_get_absolute(url: str) -> tuple[dict | list, dict[str, str]]:
    request = Request(url, headers=github_headers())
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
        response_headers = dict(response.info().items())
        return payload, response_headers


def http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> tuple[dict | list, dict[str, str], int]:
    request = Request(url, headers=headers or {}, data=data, method=method)
    with urlopen(request, timeout=10) as response:
        raw_body = response.read()
        payload: dict | list
        if raw_body:
            payload = json.loads(raw_body.decode("utf-8"))
        else:
            payload = {}
        response_headers = dict(response.info().items())
        return payload, response_headers, response.getcode()


def fetch_repo_stats(user: str) -> dict[str, int | str]:
    user_payload, _ = github_get(f"/users/{user}")

    total_stars = 0
    repo_count = 0
    next_url = f"{GITHUB_API_BASE}/users/{user}/repos?per_page=100&type=owner&sort=updated"

    while next_url:
        repos_payload, headers = github_get_absolute(next_url)
        if not isinstance(repos_payload, list):
            break

        repo_count += len(repos_payload)
        total_stars += sum(int(repo.get("stargazers_count", 0)) for repo in repos_payload)
        next_url = parse_next_link(headers.get("Link"))

    return {
        "stars": total_stars,
        "repos": repo_count,
        "followers": int(user_payload.get("followers", 0)),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "Live via GitHub API" if os.getenv("GITHUB_TOKEN") else "Live via GitHub API (no token)",
    }


def fetch_repos(user: str) -> list[dict]:
    repos: list[dict] = []
    next_url = f"{GITHUB_API_BASE}/users/{user}/repos?per_page=100&type=owner&sort=updated"

    while next_url:
        repos_payload, headers = github_get_absolute(next_url)
        if not isinstance(repos_payload, list):
            break
        repos.extend(repos_payload)
        next_url = parse_next_link(headers.get("Link"))

    return repos


def fetch_repo_languages(languages_url: str) -> dict[str, int]:
    parsed = urlparse(languages_url)
    payload, _ = github_get(parsed.path)
    if not isinstance(payload, dict):
        return {}
    return {str(key): int(value) for key, value in payload.items()}


def fetch_top_languages(user: str) -> dict[str, object]:
    repos = fetch_repos(user)
    totals: dict[str, int] = {}

    for repo in repos:
        if repo.get("fork"):
            continue
        languages_url = repo.get("languages_url")
        if not languages_url:
            continue

        for language, size in fetch_repo_languages(str(languages_url)).items():
            totals[language] = totals.get(language, 0) + size

    total_bytes = sum(totals.values())
    top_languages = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:6]

    return {
        "languages": top_languages,
        "total_bytes": total_bytes,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "Live via GitHub API" if os.getenv("GITHUB_TOKEN") else "Live via GitHub API (no token)",
    }


def fetch_public_events(user: str, pages: int = 3) -> list[dict]:
    events: list[dict] = []
    for page in range(1, pages + 1):
        payload, _ = github_get(f"/users/{user}/events/public", {"per_page": 100, "page": page})
        if not isinstance(payload, list) or not payload:
            break
        events.extend(payload)
        if len(payload) < 100:
            break
    return events


def calculate_streaks(active_dates: list[date]) -> tuple[int, int]:
    if not active_dates:
        return 0, 0

    ordered = sorted(set(active_dates))
    longest = 1
    current = 1
    running = 1

    for index in range(1, len(ordered)):
        delta = (ordered[index] - ordered[index - 1]).days
        if delta == 1:
            running += 1
            longest = max(longest, running)
        else:
            running = 1

    latest = ordered[-1]
    if latest not in {date.today(), date.today() - timedelta(days=1)}:
        current = 0
    else:
        current = 1
        cursor = latest
        while (cursor - timedelta(days=1)) in ordered:
            current += 1
            cursor -= timedelta(days=1)

    return current, longest


def fetch_streak_data(user: str) -> dict[str, object]:
    events = fetch_public_events(user)
    active_dates = []
    recent_counts: dict[date, int] = {}

    for event in events:
        created_at = event.get("created_at")
        if not created_at:
            continue
        event_day = datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).date()
        active_dates.append(event_day)
        recent_counts[event_day] = recent_counts.get(event_day, 0) + 1

    current_streak, longest_streak = calculate_streaks(active_dates)
    today = date.today()
    window_days = [today - timedelta(days=13 - index) for index in range(14)]
    sparkline = [(day.strftime("%d %b"), recent_counts.get(day, 0)) for day in window_days]

    return {
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "active_days": len(set(active_dates)),
        "event_count": len(events),
        "last_active": max(active_dates).isoformat() if active_dates else "N/A",
        "sparkline": sparkline,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "Live via GitHub API" if os.getenv("GITHUB_TOKEN") else "Live via GitHub API (no token)",
    }


def default_stats() -> dict[str, int | str]:
    return {
        "stars": 0,
        "repos": 0,
        "followers": 0,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "Fallback placeholder",
    }


def load_stats(user: str) -> dict[str, int | str]:
    try:
        return fetch_repo_stats(user)
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return default_stats()


def default_top_languages() -> dict[str, object]:
    return {
        "languages": [
            ("Python", 60),
            ("C++", 20),
            ("JavaScript", 12),
            ("Dockerfile", 8),
        ],
        "total_bytes": 100,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "Fallback placeholder",
    }


def load_top_languages(user: str) -> dict[str, object]:
    try:
        data = fetch_top_languages(user)
        if not data["languages"]:
            return default_top_languages()
        return data
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return default_top_languages()


def default_streak_data() -> dict[str, object]:
    today = date.today()
    sparkline = [((today - timedelta(days=13 - index)).strftime("%d %b"), 0) for index in range(14)]
    return {
        "current_streak": 0,
        "longest_streak": 0,
        "active_days": 0,
        "event_count": 0,
        "last_active": "N/A",
        "sparkline": sparkline,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "Fallback placeholder",
    }


def load_streak_data(user: str) -> dict[str, object]:
    try:
        return fetch_streak_data(user)
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return default_streak_data()


def static_spotify_data() -> dict[str, object]:
    return {
        "title": "Arkhino DEV",
        "subtitle": "Coding sessions, late-night builds, and synth-heavy focus loops",
        "album": "Static profile card with a direct jump to the Spotify page",
        "url": "https://open.spotify.com/user/31dfdduerefgrltei75bsz5eogpy",
        "status": "Static profile card",
    }


def svg_card(user: str, stats: dict[str, int | str]) -> str:
    safe_user = escape(user)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="900" height="180" viewBox="0 0 900 180">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{PALETTE['bg']}"/>
      <stop offset="55%" stop-color="{PALETTE['royal']}"/>
      <stop offset="100%" stop-color="{PALETTE['gold']}"/>
    </linearGradient>
    <filter id="blur" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="10" result="b"/>
      <feBlend in="SourceGraphic" in2="b" mode="screen"/>
    </filter>
    <style>
      .h {{ font: 700 30px system-ui, -apple-system, Segoe UI, Roboto, Arial; }}
      .p {{ font: 600 16px system-ui, -apple-system, Segoe UI, Roboto, Arial; }}
      .m {{ font: 500 13px system-ui, -apple-system, Segoe UI, Roboto, Arial; }}
    </style>
  </defs>

  <rect width="900" height="180" rx="22" fill="url(#g)"/>
  <g filter="url(#blur)">
    <rect x="22" y="22" width="856" height="136" rx="18" fill="{PALETTE['card']}" opacity="0.55"/>
    <rect x="22" y="22" width="856" height="136" rx="18" fill="none" stroke="{PALETTE['muted']}" opacity="0.25"/>
  </g>

  <text x="44" y="72" class="h" fill="{PALETTE['text']}">{safe_user} - Custom Stats</text>
  <text x="44" y="104" class="p" fill="{PALETTE['topaz']}">Stars: {stats['stars']}   •   Repos: {stats['repos']}   •   Followers: {stats['followers']}</text>
  <text x="44" y="132" class="m" fill="{PALETTE['muted']}">Updated: {stats['updated']} • {escape(str(stats['status']))}</text>
</svg>"""


def top_languages_card(user: str, data: dict[str, object]) -> str:
    safe_user = escape(user)
    languages = data["languages"]
    total_bytes = int(data["total_bytes"]) or 1
    bar_colors = ["#7A3F91", "#E6A520", "#C59DD9", "#FFD77A", "#F5F6F7", "#C1C4C8"]

    bar_widths = []
    for _, size in languages:
        percentage = max((int(size) / total_bytes) * 100, 2)
        bar_widths.append(percentage)

    bar_segments = []
    offset = 0.0
    for index, width in enumerate(bar_widths):
        bar_segments.append(
            f'<rect x="{44 + (812 * offset / 100):.2f}" y="86" width="{(812 * width / 100):.2f}" '
            f'height="12" fill="{bar_colors[index % len(bar_colors)]}" rx="6"/>'
        )
        offset += width

    labels = []
    for index, (language, size) in enumerate(languages):
        percentage = (int(size) / total_bytes) * 100
        labels.append(
            f'<text x="44" y="{132 + (index * 18)}" class="m" fill="{bar_colors[index % len(bar_colors)]}">'
            f'{escape(language)}: {percentage:.1f}%</text>'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="900" height="260" viewBox="0 0 900 260">
  <defs>
    <linearGradient id="g2" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{PALETTE['bg']}"/>
      <stop offset="55%" stop-color="{PALETTE['royal']}"/>
      <stop offset="100%" stop-color="{PALETTE['gold']}"/>
    </linearGradient>
    <filter id="blur2" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="10" result="b"/>
      <feBlend in="SourceGraphic" in2="b" mode="screen"/>
    </filter>
    <style>
      .h {{ font: 700 30px system-ui, -apple-system, Segoe UI, Roboto, Arial; }}
      .p {{ font: 600 16px system-ui, -apple-system, Segoe UI, Roboto, Arial; }}
      .m {{ font: 500 13px system-ui, -apple-system, Segoe UI, Roboto, Arial; }}
    </style>
  </defs>

  <rect width="900" height="260" rx="22" fill="url(#g2)"/>
  <g filter="url(#blur2)">
    <rect x="22" y="22" width="856" height="216" rx="18" fill="{PALETTE['card']}" opacity="0.55"/>
    <rect x="22" y="22" width="856" height="216" rx="18" fill="none" stroke="{PALETTE['muted']}" opacity="0.25"/>
  </g>

  <text x="44" y="60" class="h" fill="{PALETTE['text']}">{safe_user} - Top Languages</text>
  <text x="44" y="80" class="p" fill="{PALETTE['topaz']}">Non-fork repositories aggregated via GitHub API</text>
  <rect x="44" y="86" width="812" height="12" fill="{PALETTE['card']}" opacity="0.6" rx="6"/>
  {''.join(bar_segments)}
  {''.join(labels)}
  <text x="44" y="232" class="m" fill="{PALETTE['muted']}">Updated: {data['updated']} • {escape(str(data['status']))}</text>
</svg>"""


def streak_card(user: str, data: dict[str, object]) -> str:
    safe_user = escape(user)
    sparkline = data["sparkline"]
    max_count = max((count for _, count in sparkline), default=1) or 1
    bars = []

    for index, (label, count) in enumerate(sparkline):
        height = 10 + ((count / max_count) * 44 if max_count else 0)
        x = 44 + (index * 57)
        y = 186 - height
        fill = PALETTE["gold"] if count else PALETTE["soft"]
        opacity = "0.95" if count else "0.3"
        bars.append(
            f'<rect x="{x}" y="{y:.2f}" width="28" height="{height:.2f}" rx="10" fill="{fill}" opacity="{opacity}"/>'
            f'<text x="{x + 14}" y="204" text-anchor="middle" class="m" fill="{PALETTE["muted"]}">{label.split()[0]}</text>'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="900" height="260" viewBox="0 0 900 260">
  <defs>
    <linearGradient id="g3" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{PALETTE['bg']}"/>
      <stop offset="55%" stop-color="{PALETTE['royal']}"/>
      <stop offset="100%" stop-color="{PALETTE['gold']}"/>
    </linearGradient>
    <filter id="blur3" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="10" result="b"/>
      <feBlend in="SourceGraphic" in2="b" mode="screen"/>
    </filter>
    <style>
      .h {{ font: 700 30px system-ui, -apple-system, Segoe UI, Roboto, Arial; }}
      .p {{ font: 600 16px system-ui, -apple-system, Segoe UI, Roboto, Arial; }}
      .m {{ font: 500 13px system-ui, -apple-system, Segoe UI, Roboto, Arial; }}
      .k {{ font: 700 22px system-ui, -apple-system, Segoe UI, Roboto, Arial; }}
    </style>
  </defs>

  <rect width="900" height="260" rx="22" fill="url(#g3)"/>
  <g filter="url(#blur3)">
    <rect x="22" y="22" width="856" height="216" rx="18" fill="{PALETTE['card']}" opacity="0.55"/>
    <rect x="22" y="22" width="856" height="216" rx="18" fill="none" stroke="{PALETTE['muted']}" opacity="0.25"/>
  </g>

  <text x="44" y="58" class="h" fill="{PALETTE['text']}">{safe_user} - Activity Streak</text>
  <text x="44" y="82" class="p" fill="{PALETTE['topaz']}">Recent public GitHub activity, matched to the same card system</text>

  <text x="44" y="118" class="m" fill="{PALETTE['muted']}">Current</text>
  <text x="44" y="144" class="k" fill="{PALETTE['text']}">{data['current_streak']} days</text>

  <text x="218" y="118" class="m" fill="{PALETTE['muted']}">Longest</text>
  <text x="218" y="144" class="k" fill="{PALETTE['text']}">{data['longest_streak']} days</text>

  <text x="392" y="118" class="m" fill="{PALETTE['muted']}">Active Days</text>
  <text x="392" y="144" class="k" fill="{PALETTE['text']}">{data['active_days']}</text>

  <text x="566" y="118" class="m" fill="{PALETTE['muted']}">Events Window</text>
  <text x="566" y="144" class="k" fill="{PALETTE['text']}">{data['event_count']}</text>

  <text x="740" y="118" class="m" fill="{PALETTE['muted']}">Last Active</text>
  <text x="740" y="144" class="k" fill="{PALETTE['text']}">{escape(str(data['last_active']))}</text>

  <rect x="44" y="160" width="812" height="46" fill="{PALETTE['card']}" opacity="0.22" rx="14"/>
  {''.join(bars)}
  <text x="44" y="232" class="m" fill="{PALETTE['muted']}">Updated: {data['updated']} • {escape(str(data['status']))}</text>
</svg>"""


def spotify_card(data: dict[str, object]) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="900" height="220" viewBox="0 0 900 220">
  <defs>
    <linearGradient id="g4" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{PALETTE['bg']}"/>
      <stop offset="55%" stop-color="{PALETTE['royal']}"/>
      <stop offset="100%" stop-color="{PALETTE['gold']}"/>
    </linearGradient>
    <filter id="blur4" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="10" result="b"/>
      <feBlend in="SourceGraphic" in2="b" mode="screen"/>
    </filter>
    <style>
      .h {{ font: 700 30px system-ui, -apple-system, Segoe UI, Roboto, Arial; }}
      .p {{ font: 600 18px system-ui, -apple-system, Segoe UI, Roboto, Arial; }}
      .m {{ font: 500 13px system-ui, -apple-system, Segoe UI, Roboto, Arial; }}
      .chip {{ font: 700 12px system-ui, -apple-system, Segoe UI, Roboto, Arial; letter-spacing: 1px; }}
    </style>
  </defs>

  <rect width="900" height="220" rx="22" fill="url(#g4)"/>
  <g filter="url(#blur4)">
    <rect x="22" y="22" width="856" height="176" rx="18" fill="{PALETTE['card']}" opacity="0.55"/>
    <rect x="22" y="22" width="856" height="176" rx="18" fill="none" stroke="{PALETTE['muted']}" opacity="0.25"/>
  </g>

  <rect x="54" y="48" width="140" height="124" rx="24" fill="{PALETTE['bg']}" opacity="0.82"/>
  <circle cx="124" cy="103" r="38" fill="{PALETTE['gold']}" opacity="0.98"/>
  <path d="M104 89c18-5 37-3 50 5" fill="none" stroke="{PALETTE['bg']}" stroke-width="5.5" stroke-linecap="round"/>
  <path d="M109 101c14-3 29-2 40 4" fill="none" stroke="{PALETTE['bg']}" stroke-width="5.5" stroke-linecap="round"/>
  <path d="M114 113c10-2 20-1 28 3" fill="none" stroke="{PALETTE['bg']}" stroke-width="5.5" stroke-linecap="round"/>
  <text x="124" y="154" text-anchor="middle" class="chip" fill="{PALETTE['text']}">SPOTIFY</text>

  <text x="228" y="64" class="m" fill="{PALETTE['topaz']}">Spotify Profile</text>
  <text x="228" y="98" class="h" fill="{PALETTE['text']}">{escape(str(data['title']))}</text>
  <text x="228" y="128" class="p" fill="{PALETTE['soft']}">{escape(str(data['subtitle']))}</text>
  <text x="228" y="156" class="m" fill="{PALETTE['muted']}">{escape(str(data['album']))}</text>

  <rect x="228" y="170" width="196" height="30" rx="15" fill="{PALETTE['gold']}" opacity="0.96"/>
  <text x="326" y="189" text-anchor="middle" class="chip" fill="{PALETTE['bg']}">OPEN ON SPOTIFY</text>

  <rect x="706" y="98" width="16" height="60" rx="8" fill="{PALETTE['soft']}" opacity="0.55"/>
  <rect x="734" y="82" width="16" height="76" rx="8" fill="{PALETTE['gold']}" opacity="0.95"/>
  <rect x="762" y="110" width="16" height="48" rx="8" fill="{PALETTE['soft']}" opacity="0.45"/>
  <rect x="790" y="70" width="16" height="88" rx="8" fill="{PALETTE['gold']}" opacity="0.95"/>
  <rect x="818" y="92" width="16" height="66" rx="8" fill="{PALETTE['soft']}" opacity="0.55"/>
</svg>"""


@app.get("/api/stats")
def stats(user: str = "serhatvs") -> Response:
    svg = svg_card(user, load_stats(user))
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=1800"},
    )


@app.get("/api/top-langs")
def top_langs(user: str = "serhatvs") -> Response:
    svg = top_languages_card(user, load_top_languages(user))
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=1800"},
    )


@app.get("/api/streak")
def streak(user: str = "serhatvs") -> Response:
    svg = streak_card(user, load_streak_data(user))
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=1800"},
    )


@app.get("/api/spotify")
def spotify() -> Response:
    data = static_spotify_data()
    svg = spotify_card(data)
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=1800"},
    )
