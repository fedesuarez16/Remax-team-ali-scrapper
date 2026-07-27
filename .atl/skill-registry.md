# Skill Registry — multi-agent-realstate

Generated: 2026-07-18
Project: remax-team-ali-scrapper (multi-agent-realstate)

## User Skills

| Skill | Trigger |
|-------|---------|
| `branch-pr` | When creating a pull request, opening a PR, or preparing changes for review |
| `find-skills` | When user asks "how do I do X", "find a skill for X", or wants to discover skills |
| `go-testing` | When writing Go tests, using teatest, or adding test coverage |
| `issue-creation` | When creating a GitHub issue, reporting a bug, or requesting a feature |
| `judgment-day` | When user says "judgment day", "review adversarial", "dual review", "juzgar" |
| `skill-creator` | When user asks to create a new skill or document patterns for AI |

## SDD Skills (system)

`sdd-explore` · `sdd-propose` · `sdd-spec` · `sdd-design` · `sdd-tasks` · `sdd-apply` · `sdd-verify` · `sdd-archive`

## Project Conventions

- Monorepo: `frontend/` (Next.js 16 App Router, React 19, TS, Tailwind 4, Supabase JS client) + `backend/` (FastAPI, Playwright/Apify scraping, Supabase/Postgres, LangGraph) + `supabase/` (migrations).
- Deploy: frontend on Vercel, backend on Railway (Docker, `$PORT` dynamic).
- `frontend/AGENTS.md`: Next.js 16 has breaking API changes vs training data — read `node_modules/next/dist/docs/` before writing Next-specific code; heed deprecation notices.
- Global user CLAUDE.md rules apply repo-wide: conventional commits only (no AI attribution), never build after changes, use bat/rg/fd/sd/eza (never cat/grep/find/sed/ls), short default answers, verify claims before agreeing.
- Backend has pytest configured (`backend/tests/`, `pytest-asyncio`, ruff, mypy strict). Frontend has NO test runner configured (no vitest/jest in package.json) — only `lint`/`typecheck`/`build` scripts.
- Search history feature (`frontend/hooks/useSearchHistory.ts`) is currently 100% client-side `localStorage`, no backend persistence — origin-scoped, does not sync across domains/devices/browsers.

## Compact Rules

### branch-pr
- Every PR MUST link an approved issue (no exceptions)
- Every PR MUST have exactly one `type:*` label
- Automated checks must pass before merge

### go-testing
- Use `teatest` for Bubbletea TUI tests
- Table-driven tests preferred
- Run with `go test ./...`

### issue-creation
- Every issue must have a type label
- Link related issues and PRs
- Use issue-first workflow: issue before PR

### judgment-day
- Launches two independent blind judge sub-agents
- Synthesizes findings and applies fixes
- Re-judges until both pass or escalates after 2 iterations

### skill-creator
- Follow Agent Skills spec for new skills
- Include frontmatter with name, description, trigger
- Keep compact rules section for sub-agent injection
