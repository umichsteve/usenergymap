# data/contacts — sourcing rules

Contacts for FERC-registered QFs and site operators, assembled from first-party
sources only. **No paid, scraped, or third-party data vendors.** Blank cells
are preferred over guesses.

## Allowed sources

- **FERC Form 556** (QF Self-Certification/Recertification) PDFs from
  elibrary.ferc.gov. The Form 556 "Applicant Contact" (section 2) and
  "Alternate Contact" (section 3) are public filings.
- **FERC QFList** (ferconline.ferc.gov/QFList.aspx) to look up docket numbers.
- **Operator's own domain** — the company's primary website and subdomains it
  controls (e.g. `careers.operator.com`, `suppliers.operator.com`). Public
  contact / supplier / vendor-registration / procurement pages.
- **Operator-owned LinkedIn URL** is recorded only when the operator's own
  website links out to it.

## Disallowed

- LinkedIn scraping, Apollo, Lusha, ZoomInfo, RocketReach, any paid or
  third-party data vendor.
- Guessed email patterns (e.g. `firstname.lastname@…`). If the operator's own
  site does not publish an inbox, leave the cell blank.
- Mirrors, caches, proxies, or alternate endpoints after a 403 / 429 / captcha.
  See `scripts/build_contacts.py` stop rule.

## Fields per contact row

| field | source expectation |
| --- | --- |
| `project_id` / `operator_key` | from `projects.json` |
| `contact_name` / `contact_title` | Form 556 §2 (and §3 for alternate) |
| `contact_phone` / `contact_email` | Form 556, or operator domain |
| `procurement_email` / `general_phone` | operator domain only |
| `hq_city` / `hq_state` | operator domain or Form 556 §2 address |
| `linkedin_url` | only if linked from operator domain |
| `source_url` | per filled cell, always required |
| `accession_number` / `docket` | Form 556 provenance |

## Pacing

- FERC endpoints: **1 request per second**, single-threaded.
- Operator sites: at most 1 request per 2 seconds per host.
- Stop condition: any 403, 429, captcha, or bot challenge halts that pipeline
  for the whole run. No retries. Report what tripped it.
