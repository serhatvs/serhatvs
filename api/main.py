import json
import os
from datetime import datetime, timezone
from html import escape
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
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


@app.get("/api/stats")
def stats(user: str = "serhatvs") -> Response:
    svg = svg_card(user, load_stats(user))
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=1800"},
    )
