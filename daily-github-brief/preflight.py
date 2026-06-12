#!/usr/bin/env python3
"""Cheap scheduled-run preflight for the AO brief workflow.

This script intentionally uses only Python's standard library so scheduled runs can
skip dependency setup when no tracked GitHub sources have new commits.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = "https://api.github.com"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_z(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def github_get(path: str, params: dict[str, Any] | None = None) -> Any:
    query = ""
    if params:
        clean_params = {k: v for k, v in params.items() if v is not None}
        query = "?" + urlencode(clean_params)

    url = API_BASE + path + query
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ao-brief-preflight/1.0",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(url, headers=headers)
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def source_window(source_state: dict[str, Any], now: datetime) -> tuple[datetime, datetime]:
    lookback_hours = int(os.environ.get("LOOKBACK_HOURS", "30"))
    overlap_hours = int(os.environ.get("OVERLAP_HOURS", "2"))
    last_success = parse_iso_z(source_state.get("last_success_at"))
    if last_success:
        return last_success - timedelta(hours=overlap_hours), now
    return now - timedelta(hours=lookback_hours), now


def load_sources(base_dir: Path) -> list[dict[str, Any]]:
    raw = read_json(base_dir / "sources.json", [])
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("sources"), list):
        return raw["sources"]
    return []


def fetch_profile_repos(source: dict[str, Any]) -> list[dict[str, Any]]:
    profile = source["profile"]
    max_repos = int(source.get("max_repos") or os.environ.get("PROFILE_MAX_REPOS", "20"))
    include_archived = bool(source.get("include_archived", False))
    repos: list[dict[str, Any]] = []
    page = 1

    while len(repos) < max_repos:
        page_items = github_get(
            f"/users/{profile}/repos",
            {
                "sort": "updated",
                "direction": "desc",
                "per_page": min(100, max_repos),
                "page": page,
            },
        )
        if not page_items:
            break

        for item in page_items:
            if not include_archived and item.get("archived"):
                continue
            full_name = item.get("full_name")
            if full_name:
                repos.append(
                    {
                        "repo": full_name,
                        "branch": source.get("branch") or item.get("default_branch"),
                    }
                )
            if len(repos) >= max_repos:
                break

        if len(page_items) < min(100, max_repos):
            break
        page += 1

    return repos


def resolve_targets(source: dict[str, Any]) -> list[dict[str, Any]]:
    if source.get("repo"):
        return [source]

    repos = fetch_profile_repos(source)
    author = source.get("author") or source.get("profile")
    targets: list[dict[str, Any]] = []
    for repo in repos:
        target = dict(source)
        target["repo"] = repo["repo"]
        target["author"] = author
        target["branch"] = source.get("branch") or repo.get("branch")
        targets.append(target)
    return targets


def has_new_commits(target: dict[str, Any], since: datetime, until: datetime, seen_shas: set[str]) -> bool:
    owner, repo_name = target["repo"].split("/", 1)
    params: dict[str, Any] = {
        "author": target.get("author"),
        "since": iso_z(since),
        "until": iso_z(until),
        "per_page": 100,
    }
    if target.get("branch"):
        params["sha"] = target["branch"]

    commits = github_get(f"/repos/{owner}/{repo_name}/commits", params)
    for commit in commits:
        sha = commit.get("sha")
        if sha and sha not in seen_shas:
            print(f"New commit candidate: {target['repo']} {sha[:7]} {commit.get('html_url', '')}")
            return True
    return False


def write_output(has_updates: bool) -> None:
    value = "1" if has_updates else "0"
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"has_updates={value}\n")
    print(f"has_updates={value}")


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    sources = load_sources(base_dir)
    state = read_json(base_dir / "state" / "brief_memory.json", {"sources": {}})
    now = utc_now()

    if not sources:
        print("No sources found; running full workflow to avoid skipping incorrectly.")
        write_output(True)
        return 0

    try:
        for source in sources:
            sid = source.get("id")
            source_state = (state.get("sources") or {}).get(sid, {}) if sid else {}
            since, until = source_window(source_state, now)
            seen_shas = set(source_state.get("last_seen_shas", []))
            for target in resolve_targets(source):
                if has_new_commits(target, since, until, seen_shas):
                    write_output(True)
                    return 0
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
        print(f"Preflight could not safely determine update status: {exc}", file=sys.stderr)
        print("Running full workflow to avoid skipping incorrectly.")
        write_output(True)
        return 0

    print("No new tracked commits found in preflight.")
    write_output(False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
