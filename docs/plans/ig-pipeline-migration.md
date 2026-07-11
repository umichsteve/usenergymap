**Project:** usenergymap Instagram pipeline migration (pilot)
**Mode:** PLAN → BUILD (gate at Phase 1 — confirm architecture before writing pipeline code)
**Context:** umichsteve GitHub. Reference implementation: `gps-social-daily.mjs` (GPS City Guide repo — GitHub Actions + Node, posts to Instagram via Buffer's API directly). Target: usenergymap repo (this repo). Buffer account currently shared across 5 brand orgs via the `instagram-buffer-topup` daily agent task.
**Done when:** usenergymap posts to Instagram through a GitHub Actions + script pipeline, no browser/agent step, running unattended for 14 consecutive days at parity with current post quality, cadence, and dedupe reliability.
**Non-goals:** see bottom.

---

## Why this exists

Steve currently runs Instagram for 5 brands (velar.travel, GRproper, NOLAproper, usenergymap, Weekly Silicon) through a daily Claude agent task (`instagram-buffer-topup`) that logs into Buffer via Chrome and clicks through the composer UI. A second pipeline, `gps-social-daily.mjs`, does the same job for GPS City Guide with no agent or browser involved — a GitHub Actions cron runs a script that sources content, generates an image, and posts through Buffer's API directly.

usenergymap is the best candidate to port first because it already has the two things GPS City Guide had going in: structured tracker data instead of live-scraped content, and its own Python card generator instead of hand-sourced photography. The other four brands are venue-based (restaurants, neighborhoods) with no equivalent — that's a separate, harder problem for later, not in scope here.

## Your task

Port usenergymap's Instagram posting off the agent + Buffer-UI pattern and onto a GitHub Actions + script pattern, using `gps-social-daily.mjs` as the reference architecture. Don't copy it blindly — confirm what usenergymap actually needs first. It may end up simpler than the reference implementation.

---

## Phase 0 — Discovery (before writing any pipeline code)

Read, don't assume. Report findings before proceeding to Phase 1.

1. Locate the usenergymap Python card-generation script in this repo. Document: input data shape (the tracker entries), output image spec (dimensions, fonts, branding assets), and how it's currently invoked.
2. Read `gps-social-daily.mjs` end to end, plus every module it imports (catalog reader, PD-photo resolver, Haiku caption condenser, card renderer, R2 upload, Buffer GraphQL client, `posted.json` dedupe writer). This is an architecture reference, not a template to paste verbatim.
3. Read `instagram-buffer-topup`'s usenergymap-specific logic — how it currently picks what to post, and the exact shape of its dedupe state (`featured_log.json` or equivalent).
4. Find how `gps-social-daily.mjs` authenticates to Buffer (API token, org/profile ID, where the secret lives). Confirm whether usenergymap needs its own Buffer profile/token or can reuse the existing credential against a different profile ID.
5. Confirm image hosting — does the existing R2 bucket generalize to a second brand, or does this need its own bucket/prefix?
6. Check for an existing GitHub Actions workflow file behind `gps-social-daily.mjs`. If one exists, it's the starting template for cron schedule and secrets wiring.

**Flag these, don't silently decide:**
- Does usenergymap need a photo-sourcing step at all? Unlike GPS City Guide's landmarks, tracker entries likely have no public-domain-photo equivalent — the existing Python generator may already produce fully generative cards (charts/maps from data) with no real-world photo involved. If so, drop that entire piece of the reference architecture.
- Does usenergymap need an LLM captioning step? GPS City Guide needs Haiku because it's condensing long verified writeups. If usenergymap's captions are already template- or field-driven, that call may be unnecessary.
- Is the current Python image generator solid? If yes, don't rewrite it in Node for consistency — have the Actions workflow shell out to it. Only touch logic you have a real reason to touch.

## Phase 1 — Confirm architecture with Steve

Before writing pipeline code, summarize back (short — not a design doc):
- The simplified pipeline shape for usenergymap (which of the 5 reference steps are actually needed)
- Where secrets will live, and whether any new ones need to be created
- Proposed dedupe strategy (reuse `posted.json` pattern or adapt `featured_log.json`)

Wait for a go-ahead before Phase 2.

## Phase 2 — Build

Standard shape, adapted per Phase 0/1 findings:
1. Content selection — read tracker data, pick today's entry, apply dedupe.
2. Image — generate or reuse existing Python card output.
3. Caption — condense or template-fill per Phase 0 finding (may not need an LLM call).
4. Post — Buffer API (GraphQL, matching `gps-social-daily.mjs`'s contract) creates a draft/scheduled post.
5. Dedupe log write-back.

Test via `workflow_dispatch` runs against a real or Buffer-draft/unpublished target. No local Python venv, no local dev server — cloud-only, consistent with how everything else here gets built.

## Phase 3 — Shadow mode

Run the new pipeline in parallel with the existing agent task for usenergymap specifically, in a mode that logs what it *would* post without publishing (or posts to a draft state requiring manual approval). Suggested window: 7–14 days, Steve's call. Do not let both pipelines publish live at once — that double-posts.

## Phase 4 — Cutover

Remove usenergymap from `instagram-buffer-topup`'s brand list (leave the other 4 brands unchanged). Let the new pipeline run solo. Monitor for a few days.

**Rollback:** if the new pipeline fails or produces bad output, re-add usenergymap to the agent task and disable the Actions cron. Don't delete usenergymap logic from `instagram-buffer-topup` in this pass — keep it as the fallback until confidence is established.

---

## Non-goals

- Not migrating velar.travel, GRproper, NOLAproper, or Weekly Silicon in this pass — venue-based brands have no tracker-data/PD-photo equivalent and need their own plan.
- Not rebuilding the analytics/QA layer (`buffer-social-review`, `social-evening-check`) for usenergymap. Confirm in Phase 0 whether those tasks already cover usenergymap's account — they likely keep working unchanged regardless of which pipeline posts.
- Not changing Buffer org/workspace structure or other brands' credentials.
- Not modifying `gps-social-daily.mjs` itself — read-only reference. Extracting shared code into a common library is fine if it's genuinely cleaner, but not required.

## Open questions to surface, not silently decide

- Whether usenergymap should get its own scoped Buffer API token (least-privilege) vs. reusing `gps-social-daily.mjs`'s credential against a different profile ID.
- Shadow-mode window length (default suggestion: 7–14 days).
- Posting cadence — confirm daily is still correct for usenergymap.
