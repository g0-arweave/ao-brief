# Daily GitHub Brief

This repo sends one daily email that turns public GitHub commits into a simple, evidence-led progress brief.

It starts with Sam Williams on `permaweb/HyperBEAM`, but it is built so you can add more sources later by editing `sources.json`.

## What the email includes

- Top takeaway in plain English
- Public activity count
- Proof from exact commit titles and links
- Files changed plus additions/deletions when available
- Simple explanation of what changed
- Why it matters for AO
- Suggested X post
- Optional short thread when there is enough substance
- Public-work caveat

The default tone is positive, realistic, and grounded in the commits. The goal is to show progress without sounding like hype.

## Files

```text
daily-github-brief/
  brief.py                      # fetch commits, analyze, email
  sources.json                  # sources to monitor
  repo_contexts/hyperbeam.md    # durable repo context for better analysis
  state/brief_memory.json       # rolling memory, updated after each run
  requirements.txt

.github/workflows/
  daily.yml                      # 8am PT GitHub Actions schedule
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

The workflow is set to run every day at 8am Pacific time:

```yaml
schedule:
  - cron: '0 15 * * *'
  - cron: '0 16 * * *'

# Workflow gates execution to exactly 8:00am America/Los_Angeles.
```

## Add another GitHub source

Add another object to `sources.json`:

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

Then add the context file referenced by `context_file`.

## How memory works

`state/brief_memory.json` stores recent commit titles, recently seen SHAs, and a rolling narrative note for each source. After a successful email, GitHub Actions commits the updated memory back into the private repo.

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
