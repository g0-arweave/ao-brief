# Daily GitHub Brief

This repo sends one daily email that turns public GitHub commits into a simple, evidence-led progress brief. It also sends a Friday synthesis that combines the week across tracked AO, HyperBEAM, Arweave, and permaweb sources.

If every configured source has zero new public commits, the run exits cleanly without sending an email.

It starts with Sam Williams on `permaweb/HyperBEAM`, plus selected public GitHub profiles across the AO/permaweb ecosystem. Add or remove sources by editing `sources.json`.

Each sent brief can also be archived as Markdown under `docs/briefs/`, so the project can become a public archive or mailing-list surface instead of only a private email.

## What the email includes

- Short version in plain English
- Short, human-first summary before technical details
- Practical implication before technical mechanics
- Longitudinal context from prior brief memory
- Why normal builders and operators should care
- Receipts from exact commit titles, links, and PR context when available
- Source links for the active tracked sources
- Suggested X post
- Short X thread for every email
- Friday publishing kit with a short tweet, thread, punchy observations, source-backed claims, and a simple nontechnical explanation

The default tone is optimistic, realistic, and grounded in the commits. The goal is narrative compression: show progress, momentum, and practical ecosystem meaning without sounding like hype.

## Files

```text
daily-github-brief/
  brief.py                      # fetch commits, analyze, email
  sources.json                  # sources to monitor
  repo_contexts/hyperbeam.md    # durable repo context for better analysis
  repo_contexts/ao_ecosystem.md # broader AO/Arweave profile context
  state/brief_memory.json       # rolling memory, updated after each run
  state/weekly_brief_memory.json # weekly synthesis memory
  requirements.txt

docs/
  index.md                       # lightweight public landing page
  briefs/                        # generated Markdown archive

.github/workflows/
  daily.yml                      # 7am PT GitHub Actions schedule
  friday-synthesis.yml           # Friday weekly synthesis schedule
```

## Setup

### 1. Create a private GitHub repo

Create a new private repo, then copy these files into it.

### 2. Add GitHub Actions secrets

Go to:

```text
Settings > Secrets and variables > Actions > Secrets
```

Add:

```text
OPENAI_API_KEY
SENDGRID_API_KEY
EMAIL_TO
EMAIL_FROM
```

`EMAIL_FROM` must be a verified SendGrid sender or a verified domain sender.

For multiple recipients, set `EMAIL_TO` like this:

```text
you@example.com,teammate@example.com
```

### 3. Optional GitHub Actions variables

Go to:

```text
Settings > Secrets and variables > Actions > Variables
```

Optional variables:

```text
EMAIL_FROM_NAME=AO Daily
OPENAI_MODEL=gpt-5.5
SENDGRID_API_BASE=https://api.sendgrid.com
EMAIL_REPLY_TO=ops@yourdomain.com
EMAIL_LIST_UNSUBSCRIBE=<mailto:unsubscribe@yourdomain.com>, <https://yourdomain.com/unsubscribe>
PUBLIC_ARCHIVE_BASE_URL=https://yourdomain.com/briefs
MAILING_LIST_URL=https://yourdomain.com/join
```

For EU SendGrid regional subusers, use:

```text
SENDGRID_API_BASE=https://api.eu.sendgrid.com
```

### 4. Test it manually

Open the repo on GitHub, go to:

```text
Actions > Daily GitHub Brief > Run workflow
```

Leave `dry_run` as `true` first. This prints the email in the workflow log and does not send anything.

Then run it again with:

```text
dry_run=false
```

That sends the email.

## Schedule

The daily workflow is set to run every day during the 7am Pacific hour, with a backup attempt in the same hour:

```yaml
schedule:
  - cron: '7,37 14 * * *'
  - cron: '7,37 15 * * *'

# Workflow gates execution to 7am America/Los_Angeles.
```

The Friday synthesis workflow runs only on Fridays during the 7am Pacific hour and uses a 168-hour lookback:

```bash
python daily-github-brief/brief.py --weekly-synthesis --lookback-hours 168 --state state/weekly_brief_memory.json
```

It is intentionally more combinatory than the daily brief: it compresses the week into themes, source notes, the strongest proof, one suggested X post, and a short thread.

## Public Archive And Mailing List

Every non-dry-run email writes a Markdown copy to:

```text
docs/briefs/YYYY-MM-DD-daily.md
docs/briefs/YYYY-MM-DD-weekly.md
```

The archive index at `docs/briefs/index.md` is regenerated automatically.

To make this public, enable GitHub Pages for the repo and point it at the `docs/` folder. Then set:

```text
PUBLIC_ARCHIVE_BASE_URL=https://your-pages-domain/briefs
```

To let people join the list, create a signup page with your newsletter provider and set:

```text
MAILING_LIST_URL=https://your-signup-page
```

The script will add `Read/share this brief` and `Join the list` links to the bottom of each email when those variables are present. Avoid putting a SendGrid API key directly into any public signup form; use a newsletter provider form or a small backend endpoint.

## Add another GitHub source

Add another object to `sources.json`. To track one author on one repo, use:

```json
{
  "id": "example-source",
  "label": "Example Person on Example Repo",
  "repo": "owner/repo",
  "author": "github_username",
  "branch": null,
  "context_file": "repo_contexts/example.md",
  "public_commits_url": "https://github.com/owner/repo/commits?author=github_username",
  "audience_note": "Keep it plain and useful for non-engineers."
}
```

To track a public profile across recently updated public repos, use:

```json
{
  "id": "example-profile",
  "label": "Example public repos",
  "profile": "github_username",
  "author": "github_username",
  "branch": null,
  "max_repos": 20,
  "context_file": "repo_contexts/example.md",
  "public_commits_url": "https://github.com/github_username",
  "audience_note": "Keep it plain and useful for non-engineers."
}
```

Then add the context file referenced by `context_file`.

## How memory works

`state/brief_memory.json` stores recent commit titles, recently seen SHAs, and a rolling narrative note for each source. After a successful email, GitHub Actions commits the updated memory back into the private repo. If there are no new commits, the script still records the successful check time so the next run starts from the right window, but it does not send an email.

This helps the model interpret today’s work in the context of prior days instead of treating each run like an isolated commit summary.

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Export your environment variables, then run:

```bash
python brief.py --dry-run
```

To include short diff snippets in the model input, run:

```bash
INCLUDE_PATCH_SNIPPETS=1 python brief.py --dry-run
```

By default, the email uses commit titles, links, changed files, and change counts as proof. That keeps the email readable for non-engineers.
