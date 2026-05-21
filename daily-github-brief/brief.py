#!/usr/bin/env python3
"""
Daily GitHub build brief.

Fetches public GitHub commits from configured sources, asks OpenAI for a plain-English AO progress brief, then sends one email through SendGrid.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from openai import OpenAI

PT = ZoneInfo("America/Los_Angeles")
MEMORY_START = "<<<MEMORY_UPDATE_JSON>>>"
MEMORY_END = "<<<END_MEMORY_UPDATE_JSON>>>"


@dataclass
class RunConfig:
    sources_path: Path
    state_path: Path
    dry_run: bool
    lookback_hours: int
    overlap_hours: int
    max_commit_details: int
    include_patch_snippets: bool
    openai_model: str
    sendgrid_api_base: str


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


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def build_github_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/vnd.github+json",
            "User-Agent": "daily-github-brief/1.0",
        }
    )
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        session.headers.update({"Authorization": f"Bearer {token}"})
    return session


def github_get(session: requests.Session, url: str, params: dict[str, Any] | None = None) -> Any:
    response = session.get(url, params=params, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(
            f"GitHub API error {response.status_code} for {response.url}: {response.text[:500]}"
        )
    return response.json()


def source_window(source_state: dict[str, Any], now: datetime, lookback_hours: int, overlap_hours: int) -> tuple[datetime, datetime]:
    last_success = parse_iso_z(source_state.get("last_success_at"))
    if last_success:
        since = last_success - timedelta(hours=overlap_hours)
    else:
        since = now - timedelta(hours=lookback_hours)
    return since, now


def commit_title(message: str) -> str:
    first = (message or "").splitlines()[0].strip()
    return first or "Untitled commit"


def first_lines(message: str, limit: int = 6) -> str:
    lines = [line.rstrip() for line in (message or "").splitlines()]
    return "\n".join(lines[:limit]).strip()


def summarize_files(files: list[dict[str, Any]], max_files: int = 8) -> list[dict[str, Any]]:
    summarized: list[dict[str, Any]] = []
    for f in files[:max_files]:
        summarized.append(
            {
                "filename": f.get("filename"),
                "status": f.get("status"),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "changes": f.get("changes", 0),
            }
        )
    return summarized


def patch_snippets(files: list[dict[str, Any]], max_files: int = 3, max_lines_per_file: int = 14) -> list[dict[str, str]]:
    snippets: list[dict[str, str]] = []
    for f in files:
        patch = f.get("patch")
        if not patch:
            continue
        lines: list[str] = []
        for line in patch.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("@@") or line.startswith("+") or line.startswith("-"):
                lines.append(line[:220])
            if len(lines) >= max_lines_per_file:
                break
        if lines:
            snippets.append({"filename": f.get("filename", "unknown"), "snippet": "\n".join(lines)})
        if len(snippets) >= max_files:
            break
    return snippets


def fetch_commits_for_source(
    session: requests.Session,
    source: dict[str, Any],
    since: datetime,
    until: datetime,
    max_commit_details: int,
    include_patch_snippets: bool,
) -> list[dict[str, Any]]:
    repo = source["repo"]
    owner, repo_name = repo.split("/", 1)
    base_url = f"https://api.github.com/repos/{owner}/{repo_name}/commits"

    params: dict[str, Any] = {
        "author": source.get("author"),
        "since": iso_z(since),
        "until": iso_z(until),
        "per_page": 100,
    }
    if source.get("branch"):
        params["sha"] = source["branch"]

    commits: list[dict[str, Any]] = []
    page = 1
    while True:
        params["page"] = page
        page_items = github_get(session, base_url, params=params)
        if not page_items:
            break
        commits.extend(page_items)
        if len(page_items) < 100 or page >= 10:
            break
        page += 1

    commits.sort(key=lambda c: c.get("commit", {}).get("author", {}).get("date", ""))

    detailed: list[dict[str, Any]] = []
    for index, item in enumerate(commits):
        sha = item.get("sha")
        message = item.get("commit", {}).get("message", "")
        detail: dict[str, Any] | None = None
        if sha and index < max_commit_details:
            detail_url = f"{base_url}/{sha}"
            try:
                detail = github_get(session, detail_url)
            except Exception as exc:
                print(f"Warning: could not fetch commit detail for {sha[:7]}: {exc}", file=sys.stderr)

        files = (detail or {}).get("files", [])
        stats = (detail or {}).get("stats", {})
        detailed.append(
            {
                "sha": sha,
                "short_sha": sha[:7] if sha else None,
                "title": commit_title(message),
                "message_excerpt": first_lines(message),
                "date": item.get("commit", {}).get("author", {}).get("date"),
                "url": item.get("html_url"),
                "author_login": (item.get("author") or {}).get("login"),
                "stats": {
                    "additions": stats.get("additions", 0),
                    "deletions": stats.get("deletions", 0),
                    "total": stats.get("total", 0),
                    "files_changed": len(files),
                },
                "files": summarize_files(files),
                "patch_snippets": patch_snippets(files) if include_patch_snippets else [],
            }
        )

    return detailed


def aggregate_stats(commits: list[dict[str, Any]]) -> dict[str, Any]:
    additions = sum(int(c.get("stats", {}).get("additions") or 0) for c in commits)
    deletions = sum(int(c.get("stats", {}).get("deletions") or 0) for c in commits)
    files_changed = sum(int(c.get("stats", {}).get("files_changed") or 0) for c in commits)
    titles = [c.get("title") for c in commits if c.get("title")]
    return {
        "commit_count": len(commits),
        "additions": additions,
        "deletions": deletions,
        "files_changed": files_changed,
        "commit_titles": titles,
    }


def build_payload(
    sources: list[dict[str, Any]],
    state: dict[str, Any],
    cfg: RunConfig,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    session = build_github_session()
    source_results: list[dict[str, Any]] = []
    commits_by_source: dict[str, list[dict[str, Any]]] = {}
    base_dir = cfg.sources_path.parent

    for source in sources:
        sid = source["id"]
        source_state = state.setdefault("sources", {}).setdefault(sid, {})
        since, until = source_window(source_state, now, cfg.lookback_hours, cfg.overlap_hours)
        seen_shas = set(source_state.get("last_seen_shas", []))

        commits = fetch_commits_for_source(
            session=session,
            source=source,
            since=since,
            until=until,
            max_commit_details=cfg.max_commit_details,
            include_patch_snippets=cfg.include_patch_snippets,
        )
        new_commits = [c for c in commits if c.get("sha") not in seen_shas]
        commits_by_source[sid] = new_commits

        context_path = base_dir / source.get("context_file", "") if source.get("context_file") else None
        context = read_text(context_path) if context_path else ""

        source_results.append(
            {
                "id": sid,
                "label": source.get("label", sid),
                "repo": source.get("repo"),
                "author": source.get("author"),
                "branch": source.get("branch"),
                "public_commits_url": source.get("public_commits_url"),
                "audience_note": source.get("audience_note"),
                "window_start_utc": iso_z(since),
                "window_end_utc": iso_z(until),
                "prior_memory": {
                    "rolling_context": source_state.get("rolling_context", ""),
                    "current_phase": source_state.get("current_phase", ""),
                    "recent_themes": source_state.get("recent_themes", []),
                    "last_public_activity_at": source_state.get("last_public_activity_at"),
                },
                "repo_context": context,
                "stats": aggregate_stats(new_commits),
                "commits": new_commits,
            }
        )

    payload = {
        "run_date_pt": now.astimezone(PT).strftime("%Y-%m-%d"),
        "run_time_pt": now.astimezone(PT).strftime("%Y-%m-%d %H:%M %Z"),
        "important_limits": [
            "This only analyzes visible public commits from GitHub.",
            "Do not infer private work, intent, or unreleased plans unless directly supported by the commits.",
        ],
        "sources": source_results,
    }
    return payload, commits_by_source


def build_instructions() -> str:
    return textwrap.dedent(
        f"""
        You write a daily AO GitHub progress email.

        Required style:
        - Positive, realistic, and grounded. Default toward progress when the commit evidence supports it.
        - Make the reader feel: there is real progress happening on AO, and here is the proof.
        - Use concrete proof from commits: exact commit titles, commit links, files changed, additions, deletions, and practical implications.
        - Keep the analysis simple enough for non-engineers. Explain any technical term in normal language.
        - Do not overhype, shill, or make claims beyond the public commits.
        - Do not use generic filler like "foundational work that helps the ecosystem scale".
        - Avoid contrast-template writing such as "people think X, but really Y".
        - Avoid "most people miss", "quietly becoming", "not the loudest", "boring machinery", and "this is the kind of work".
        - Do not say "runtime" without translating it. Prefer "the software layer that runs AO apps and processes".
        - Use short paragraphs and natural human wording.

        Required email structure:
        AO Daily Build Brief
        Date: <date>

        Top takeaway:
        One sentence that ties the day together in plain English. It should be specific, positive, and evidence-backed.

        <One section per source>
        Public activity:
        - Commit count
        - Files changed
        - Additions and deletions, if available
        - Time window

        Proof from commits:
        List the most important commits with exact title, link, and a short practical translation.

        Plain-English read:
        Explain what this means without deep protocol jargon.

        Why this matters for AO:
        Tie it to the larger AO vision in one or two concrete implications. Examples: easier developer setup, safer message handling, cleaner operation, fewer edge cases, easier to build real AO apps.

        What this suggests:
        A restrained read on the current direction, such as release cleanup, developer experience, safety, maintenance, or architecture cleanup. Keep it humble.

        Suggested X post:
        Write one polished post for X. It should sound human, positive, concise, and evidence-led. No thread unless there is enough substance.

        Optional thread:
        Only include this if the commit evidence is strong enough for a short 2 to 4 post thread. Otherwise write: "No thread recommended today."

        Public-work caveat:
        One sentence noting that this reflects public GitHub commits, not everything the person may have worked on.

        At the very end, include a machine-readable memory update between these exact markers. Do not put these markers in the email body before the end.
        {MEMORY_START}
        {{
          "source_notes": {{
            "source_id": {{
              "rolling_context": "5 to 8 sentence durable context about the source trajectory, updated with today's evidence.",
              "current_phase": "short phrase",
              "recent_themes": ["theme 1", "theme 2", "theme 3"]
            }}
          }}
        }}
        {MEMORY_END}
        """
    ).strip()


def call_openai(payload: dict[str, Any], cfg: RunConfig) -> tuple[str, dict[str, Any]]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=cfg.openai_model,
        instructions=build_instructions(),
        input=json.dumps(payload, indent=2, ensure_ascii=False),
        max_output_tokens=int(os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", "4500")),
    )
    text = getattr(response, "output_text", None)
    if not text:
        text = str(response)
    return strip_memory_update(text)


def strip_memory_update(text: str) -> tuple[str, dict[str, Any]]:
    pattern = re.compile(
        re.escape(MEMORY_START) + r"\s*(.*?)\s*" + re.escape(MEMORY_END),
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return text.strip(), {}
    email_text = pattern.sub("", text).strip()
    raw_json = match.group(1).strip()
    try:
        memory = json.loads(raw_json)
    except json.JSONDecodeError:
        memory = {}
    return email_text, memory


def email_subject(payload: dict[str, Any]) -> str:
    labels = [s.get("label") for s in payload.get("sources", []) if s.get("label")]
    if len(labels) == 1:
        return f"AO Daily: {labels[0]} - {payload.get('run_date_pt')}"
    return f"AO Daily: {len(labels)} GitHub sources - {payload.get('run_date_pt')}"


def text_to_html(text: str) -> str:
    escaped = html.escape(text)
    return f"""
<!doctype html>
<html>
  <body style="margin:0; padding:24px; background:#ffffff; color:#111111;">
    <div style="max-width:760px; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif; font-size:15px; line-height:1.55;">
      <pre style="white-space:pre-wrap; font-family:inherit; margin:0;">{escaped}</pre>
    </div>
  </body>
</html>
""".strip()


def parse_recipients(value: str) -> list[dict[str, str]]:
    emails = [email.strip() for email in value.split(",") if email.strip()]
    if not emails:
        raise RuntimeError("EMAIL_TO is empty")
    return [{"email": email} for email in emails]


def send_email(subject: str, text_body: str, cfg: RunConfig) -> None:
    api_key = os.environ.get("SENDGRID_API_KEY")
    email_to = os.environ.get("EMAIL_TO", "")
    email_from = os.environ.get("EMAIL_FROM", "")
    email_from_name = os.environ.get("EMAIL_FROM_NAME", "AO Daily")

    missing = [name for name, value in [("SENDGRID_API_KEY", api_key), ("EMAIL_TO", email_to), ("EMAIL_FROM", email_from)] if not value]
    if missing:
        raise RuntimeError(f"Missing required email environment variables: {', '.join(missing)}")

    payload = {
        "personalizations": [{"to": parse_recipients(email_to)}],
        "from": {"email": email_from, "name": email_from_name},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text_body},
            {"type": "text/html", "value": text_to_html(text_body)},
        ],
    }

    url = cfg.sendgrid_api_base.rstrip("/") + "/v3/mail/send"
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if response.status_code >= 300:
        raise RuntimeError(f"SendGrid error {response.status_code}: {response.text[:1000]}")


def update_state(
    state: dict[str, Any],
    payload: dict[str, Any],
    commits_by_source: dict[str, list[dict[str, Any]]],
    memory: dict[str, Any],
    now: datetime,
) -> None:
    state.setdefault("global", {})["last_success_at"] = iso_z(now)
    state.setdefault("sources", {})
    source_notes = memory.get("source_notes", {}) if isinstance(memory, dict) else {}

    for source_payload in payload.get("sources", []):
        sid = source_payload.get("id")
        if not sid:
            continue
        source_state = state["sources"].setdefault(sid, {})
        source_state["last_success_at"] = iso_z(now)

        commits = commits_by_source.get(sid, [])
        new_shas = [c.get("sha") for c in commits if c.get("sha")]
        old_shas = source_state.get("last_seen_shas", [])
        source_state["last_seen_shas"] = list(dict.fromkeys(new_shas + old_shas))[:300]

        if commits:
            latest_date = max((c.get("date") or "" for c in commits), default="")
            source_state["last_public_activity_at"] = latest_date or source_state.get("last_public_activity_at")
            titles = [c.get("title") for c in commits if c.get("title")]
            old_titles = source_state.get("recent_commit_titles", [])
            source_state["recent_commit_titles"] = list(dict.fromkeys(titles + old_titles))[:80]

        note = source_notes.get(sid) or source_notes.get(source_payload.get("label"))
        if isinstance(note, dict):
            for key in ["rolling_context", "current_phase", "recent_themes"]:
                if key in note:
                    source_state[key] = note[key]


def fallback_email(payload: dict[str, Any]) -> str:
    lines = ["AO Daily Build Brief", f"Date: {payload.get('run_date_pt')}", ""]
    lines.append("Top takeaway:")
    lines.append("There was public GitHub activity to review, but the OpenAI analysis step was not available.")
    lines.append("")
    for source in payload.get("sources", []):
        stats = source.get("stats", {})
        lines.append(source.get("label", source.get("id", "Source")))
        lines.append("Public activity:")
        lines.append(f"- Commits: {stats.get('commit_count', 0)}")
        lines.append(f"- Files changed: {stats.get('files_changed', 0)}")
        lines.append(f"- Additions/deletions: +{stats.get('additions', 0)} / -{stats.get('deletions', 0)}")
        lines.append("")
        lines.append("Proof from commits:")
        commits = source.get("commits", [])
        if not commits:
            lines.append("- No new public commits in this window.")
        for commit in commits[:10]:
            lines.append(f"- \"{commit.get('title')}\" - {commit.get('url')}")
        lines.append("")
    lines.append("Public-work caveat:")
    lines.append("This reflects visible public GitHub commits only, not everything the person may have worked on privately.")
    return "\n".join(lines)


def load_sources(path: Path) -> list[dict[str, Any]]:
    raw = read_json(path, [])

    if isinstance(raw, list):
        sources = raw
    elif isinstance(raw, dict) and isinstance(raw.get("sources"), list):
        sources = raw["sources"]
    else:
        sources = []

    if not sources:
        raise RuntimeError(
            f"sources.json must contain a non-empty list (checked: {path})"
        )

    return sources


def parse_args() -> RunConfig:
    parser = argparse.ArgumentParser(description="Send a daily GitHub progress brief by email.")
    parser.add_argument("--sources", default="sources.json", help="Path to sources.json")
    parser.add_argument("--state", default="state/brief_memory.json", help="Path to persistent state JSON")
    parser.add_argument("--dry-run", action="store_true", help="Print the email instead of sending it or updating state")
    parser.add_argument("--lookback-hours", type=int, default=int(os.environ.get("LOOKBACK_HOURS", "30")))
    parser.add_argument("--overlap-hours", type=int, default=int(os.environ.get("OVERLAP_HOURS", "2")))
    parser.add_argument("--max-commit-details", type=int, default=int(os.environ.get("MAX_COMMIT_DETAILS", "20")))
    parser.add_argument("--include-patch-snippets", action="store_true", default=os.environ.get("INCLUDE_PATCH_SNIPPETS", "0") == "1")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent

    return RunConfig(
        sources_path=(base_dir / args.sources).resolve() if not Path(args.sources).is_absolute() else Path(args.sources),
        state_path=(base_dir / args.state).resolve() if not Path(args.state).is_absolute() else Path(args.state),
        dry_run=args.dry_run,
        lookback_hours=args.lookback_hours,
        overlap_hours=args.overlap_hours,
        max_commit_details=args.max_commit_details,
        include_patch_snippets=args.include_patch_snippets,
        openai_model=os.environ.get("OPENAI_MODEL") or "gpt-5.5",
        sendgrid_api_base=os.environ.get("SENDGRID_API_BASE") or "https://api.sendgrid.com",
    )


def main() -> int:
    cfg = parse_args()
    sources = load_sources(cfg.sources_path)

    state = read_json(cfg.state_path, {"global": {"last_success_at": None}, "sources": {}})
    now = utc_now()

    payload, commits_by_source = build_payload(sources, state, cfg, now)
    subject = email_subject(payload)

    try:
        email_text, memory = call_openai(payload, cfg)
    except Exception as exc:
        print(f"Warning: OpenAI analysis failed, using fallback email: {exc}", file=sys.stderr)
        email_text, memory = fallback_email(payload), {}

    if cfg.dry_run:
        print(f"Subject: {subject}\n")
        print(email_text)
        return 0

    send_email(subject, email_text, cfg)
    update_state(state, payload, commits_by_source, memory, now)
    write_json(cfg.state_path, state)
    print(f"Sent: {subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
