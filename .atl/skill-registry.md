# Skill Registry — multi-agent-realstate

Generated: 2026-05-28
Project: multi-agent-realstate

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

None detected — project is new and empty.

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
