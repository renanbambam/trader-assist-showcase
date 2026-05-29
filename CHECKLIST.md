# Pre-Publish Checklist

Go through every item before making the repository public.

---

## Secrets & Credentials

- [ ] No API keys anywhere (search: `grep -r "sk-ant" .` and `grep -r "api_key" .`)
- [ ] No broker credentials (MT5 login, password, server name)
- [ ] No Telegram bot token or chat ID
- [ ] No database connection strings with real credentials
- [ ] `.env` file is NOT included — only `.env.example` with placeholder values
- [ ] No hardcoded IPs, internal hostnames, or VPN endpoints

## Proprietary Logic

- [ ] No trading strategy conditions or entry/exit rules in any snippet
- [ ] No prompt templates or prompt content included
- [ ] `snippets/` directory contains only architecture patterns — no business logic
- [ ] Confidence score weights are not exposed (or have been intentionally modified)
- [ ] No automation triggers, cron schedules revealing business hours or assets targeted

## Visual Content

- [ ] All SVG screenshots use placeholder/fictional data (no real trade history)
- [ ] Asset names in screenshots are generic or well-known (not specific to your broker)
- [ ] Prices and P&L values in screenshots are clearly fictional (round numbers, not real fills)
- [ ] No real account numbers, broker names, or server identifiers visible

## README & Docs

- [ ] README renders correctly on GitHub (check all image paths load)
- [ ] All badge shields resolve (click each one)
- [ ] No broken internal links
- [ ] Environment variables table uses placeholder values, not real values
- [ ] No TODO, FIXME, or "temporary" comments left in markdown

## Repository Setup (GitHub)

- [ ] Repository visibility set to **Public**
- [ ] Repository description filled in (one-line summary)
- [ ] Topics/tags added: `python`, `fastapi`, `clean-architecture`, `websocket`, `ai`, `trading`, `postgresql`, `redis`
- [ ] Social preview image set (use the dashboard SVG or a custom banner)
- [ ] Default branch is `main` (not `master`)
- [ ] No unnecessary branches left over from local development

## Code Quality

- [ ] Python snippets have consistent formatting (run `black snippets/` if needed)
- [ ] No syntax errors in any `.py` file (`python -m py_compile snippets/*.py`)
- [ ] No `print()` debug statements left in snippets
- [ ] Import statements are clean and consistent

## Professional Polish

- [ ] README opening paragraph is compelling and clear to a non-user
- [ ] Architecture diagram labels are readable at normal GitHub zoom level
- [ ] Tech stack table is accurate and up to date
- [ ] Project structure tree matches the actual codebase structure
- [ ] Quick Start commands have been tested end-to-end at least once

---

## After Publishing

- [ ] Share the repo URL in your GitHub profile's pinned repositories
- [ ] Add a link in your LinkedIn "Featured" section
- [ ] Update your resume with the GitHub URL
- [ ] Check that the README renders well on mobile (GitHub mobile app)
