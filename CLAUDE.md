# usenergymap

US data center and BESS project tracker at usenergymap.com — an interactive Leaflet map served as a static site with no build step.

## Stack

- Static HTML/JS (index.html + projects.json, Leaflet over CARTO tiles); Vercel serverless functions in api/ (Stripe checkout + exceljs dataset export); Python ingest scripts
- Deploy: Vercel (README: push to main auto-deploys; vercel.json sets cache headers)
- No build step; data refresh via scripts/ingest_eia.py and .github/workflows/refresh-eia.yml

## Conventions

- Cloud-only workflow: no local dev servers or venvs; verify via preview deploys.
- Note: repo currently on branch plan/ig-pipeline-migration — migration/scaffold in progress; confirm target branch before big changes.
- Projects are added or edited by hand in projects.json (schema_version 1.0, documented in README).

## Non-goals

- Don't add frameworks, build steps, or dependencies not already present without asking.
- Keep it a no-build static site — the whole app lives in index.html (README).

## Handoff protocol

If `HANDOFF.md` exists at the repo root: read it before anything else, treat its "Done when" as acceptance criteria and "Non-goals" as hard boundaries, run its Verify steps, and delete the file in the final commit. Format and full rules: `.claude/skills/pr-handoff/SKILL.md`.
