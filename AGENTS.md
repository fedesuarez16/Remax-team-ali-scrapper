# Repository Guidelines

## Project Structure & Module Organization

This is a two-runtime real-estate search application. `frontend/` contains the Next.js 16 App Router UI: routes are in `app/`, reusable UI in `components/`, helpers in `lib/`, and static files in `public/`. `backend/app/` contains the FastAPI API, LangGraph workflows, models, services, and ARQ tasks; tests mirror these areas in `backend/tests/`. Supabase configuration, migrations, and seed data live in `supabase/`. Operational notes belong in `docs/`.

## Build, Test, and Development Commands

- `supabase start` starts local Postgres, Auth, and Studio.
- `docker compose up` runs the FastAPI server, ARQ worker, and Redis.
- `cd frontend && pnpm install && pnpm dev` serves the UI at `localhost:3000`.
- `cd frontend && pnpm build` creates a production build.
- `cd frontend && pnpm lint && pnpm typecheck` runs ESLint and strict TypeScript checks.
- `cd backend && uv sync --dev` installs Python 3.12 dependencies from `uv.lock`.
- `cd backend && uv run pytest` runs tests; add a path or `-k expression` to focus them.
- `cd backend && uv run ruff check app tests && uv run mypy app` runs lint and strict type checks.

## Coding Style & Naming Conventions

Use four spaces in Python, type annotations for public APIs, `snake_case` for modules/functions, and `PascalCase` for classes. Ruff targets Python 3.12 with a 100-character line limit. TypeScript is strict; use `PascalCase` for components, `camelCase` for functions and variables, and `@/` imports. Follow the surrounding quote and semicolon style. Name migrations `YYYYMMDDHHMMSS_description.sql`; never edit an applied migration.

## Testing Guidelines

Pytest and `pytest-asyncio` are configured in `backend/pyproject.toml`; async tests need no marker. Name files `test_<behavior>.py` and functions `test_<expected_outcome>`, placing them in the matching service, API, graph, or core directory. Add regression coverage for fixes and mock external HTTP, Supabase, and scraping behavior. No frontend test runner is configured, so run lint, typecheck, and build for UI changes.

## Commit & Pull Request Guidelines

Meaningful recent commits use subjects such as `fix(zonaprop): ...`, `feat(zonaprop): ...`, and `chore: ...`; follow that pattern and avoid older placeholder subjects. Keep commits focused. Pull requests should explain the change, list verification commands, link issues, note migrations or configuration changes, and include screenshots for UI work.

## Security & Configuration

Keep credentials in ignored `.env` or `.env.local` files. Never commit tokens, cookies, proxy passwords, production data, or captured pages containing secrets. Sanitize logs and test fixtures before committing them.
