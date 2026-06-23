# Repository Guidelines

## Project Structure & Module Organization

- `start.sh` — one-command launcher (backend + frontend)
- `backend/app/main.py` — FastAPI entry point & route registration
- `backend/app/config.py` — paths, cache policy, API constants
- `backend/app/db.py` — SQLite schema & connection
- `backend/app/models.py` — Pydantic models
- `backend/app/engine/` — core logic: universe, data, scoring, risk, signals, backtest, selection
- `backend/app/routers/` — API routers: signal, holdings, portfolio, etf, market, backtest
- `backend/cron_update.py` — scheduled data refresh script
- `frontend/src/App.tsx` — routes & sidebar layout
- `frontend/src/api/` — API client & hooks
- `frontend/src/pages/` — page components
- `frontend/src/utils/` — CSV export & signal helpers
- `data/` — SQLite databases & logs (gitignored)

## Build, Test, and Development Commands

| Command | Directory | Purpose |
|---|---|---|
| `pip install -r requirements.txt` | `backend/` | Install Python dependencies |
| `uvicorn app.main:app --reload --port 8000` | `backend/app/` | Start backend dev server |
| `python cron_update.py` | `backend/` | Run full data refresh manually |
| `npm install` | `frontend/` | Install Node dependencies |
| `npm run dev` | `frontend/` | Start Vite dev server (port 5173) |
| `npm run build` | `frontend/` | Type-check then build for production |
| `npm run lint` | `frontend/` | Run ESLint |
| `bash start.sh` | root | Launch both servers at once |

Backend API docs available at `http://localhost:8000/docs` when running.

## Coding Style & Naming Conventions

**Python (backend):**
- PEP 8 with 4-space indentation.
- `snake_case` for functions, variables, modules; `UPPER_CASE` for constants.
- Scoring modes: `rank_momentum`, `mixed`, `pure20`, `sector_rotation` (RPS + volume ratio dual confirmation).
- Docstrings on modules and public functions.
- Pydantic models in `models.py`; keep routers thin — core logic lives in `engine/`.

**TypeScript / React (frontend):**
- 2-space indentation, enforced by ESLint (`eslint.config.js`).
- `PascalCase` for components and page files; `camelCase` for utilities and hooks.
- Functional components with default exports; React Router for navigation.
- API calls wrapped in hooks under `src/api/`.
- Run `npm run lint` before committing frontend changes.

## Testing Guidelines

- No formal test suite is currently configured. If adding tests:
  - Backend: use `pytest` with FastAPI's `TestClient`.
  - Frontend: consider Vitest + React Testing Library.
- Name test files as `test_<module>.py` (Python) or `<Component>.test.tsx` (React).

## Commit & Pull Request Guidelines

- Write commits in Chinese (matching existing author comments). Use concise, imperative-style messages.
- Keep pull requests focused on a single feature or fix.
- Include screenshots for UI changes.
- Do not commit `data/` contents — these are gitignored.

## Environment & Security Notes

- Database files live in `data/` and contain personal holdings/signal records; never commit them.
- `.env` and `.env.local` are gitignored — place sensitive keys there.
- CORS is wide open in dev; lock down before deploying publicly.
