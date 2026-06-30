---
name: reviewer
description: >
  Full-stack project reviewer that analyses code, detects bugs, fixes errors, and optimizes performance.
  Use this skill whenever the user asks to review, audit, debug, fix, or optimize a project — even
  casually phrased ("check my code", "something's broken", "make this faster", "clean this up").
  Designed for projects using React/TypeScript/Vite on the frontend and FastAPI/Python on the backend,
  but applies to any codebase. Triggers on: "review my project", "find bugs", "optimize this",
  "audit the code", "something is wrong", "fix errors", "improve performance", "code review".
---

# Reviewer Skill

A structured skill for analysing a project end-to-end, detecting bugs and errors, applying fixes, and
optimizing performance. Priority order: **bugs → performance → code quality → security**.

---

## Phase 1 — Reconnaissance

Before touching any file, build a mental model of the project.

```
1. List the repo root (view /)
2. Read key config files: package.json, pyproject.toml / requirements.txt, vite.config.*, tsconfig.json, alembic.ini, docker-compose.yml
3. Identify entry points: main.tsx / main.py / app.py
4. Skim the directory tree — note which layers exist (frontend, backend, DB, infra)
```

Record for yourself:
- **Stack confirmed** (versions of React, FastAPI, SQLAlchemy, etc.)
- **Project purpose** (what does this app do?)
- **Obvious hot-spots** (large files, deeply nested logic, any TODO/FIXME/HACK comments)

---

## Phase 2 — Bug Detection

Work through each layer systematically. For each issue found, log it in the tracker format below.

### 2a. TypeScript / React frontend
- Run a mental type-check: look for `any` casts hiding real type errors
- Check React hooks: missing deps in `useEffect`, stale closures, effects that don't clean up
- TanStack Query: missing `queryKey` dependencies, mutations that don't invalidate the right keys
- React Router: routes that don't handle 404, missing loaders/error boundaries
- Axios: interceptors that swallow errors, no timeout set, missing error handling on `.catch`
- Async/await misuse: `Promise` returned but not awaited, unhandled rejections
- Chart.js / Recharts: data shape mismatches, missing null guards before render

### 2b. Python / FastAPI backend
- Pydantic v2 validators: deprecated v1 patterns (`@validator` → `@field_validator`, `orm_mode` → `model_config`)
- SQLAlchemy 2.0 async: sessions not closed, missing `await`, `select()` vs legacy `Query` API
- FastAPI: missing `response_model`, wrong HTTP status codes, sync functions inside async routes
- Alembic: migration heads that diverge, autogenerate that misses relationship changes
- APScheduler: jobs registered multiple times on reload, missing `coalesce`, no error handler
- aiosmtplib: missing `await` on `send_message`, unclosed connections
- JWT (python-jose): algorithms not pinned, missing `aud`/`iss` validation
- passlib: deprecated hashing schemes, missing `CryptContext` configuration

### 2c. Database layer
- N+1 queries: ORM calls inside loops without `selectinload`/`joinedload`
- Missing indexes on foreign keys and filter columns
- Alembic migrations that drop columns without a transition period
- Transactions not committed or rolled back on error

### 2d. Infrastructure / config
- Secrets hardcoded or committed (scan for `password =`, `secret =`, `API_KEY =` literals)
- Docker Compose: services without `depends_on`, missing healthchecks, bind mounts in production
- CORS: `allow_origins=["*"]` in a non-dev context

---

## Phase 3 — Performance Optimization

After bugs, focus on performance bottlenecks.

### Frontend
- Bundle size: large imports not tree-shaken (`import * as`), missing `React.lazy` / `Suspense` for routes
- TanStack Query: `staleTime` set to 0 causing unnecessary refetches, no `gcTime` tuning
- Re-renders: components that re-render on every parent update without `memo`, expensive derivations not in `useMemo`
- Chart.js: datasets rebuilt on every render instead of being stable references

### Backend
- Async: sync I/O (file reads, `requests` library) called inside async FastAPI handlers — use `run_in_executor` or switch to async equivalents
- SQLAlchemy: missing `lazy="selectin"` on relationships accessed in API responses
- Missing DB connection pool settings (`pool_size`, `max_overflow`)
- APScheduler jobs doing heavy work synchronously — offload to `asyncio.create_task`
- ReportLab / OpenPyXL generation blocking the event loop — wrap in `run_in_executor`

---

## Phase 4 — Code Quality

Apply after bugs and performance are handled.

- Dead code: unused imports, functions never called, commented-out blocks
- Magic numbers / strings: extract to named constants or enums
- Duplication: identical logic in multiple places — suggest a shared utility
- Error messages: vague `except Exception: pass` blocks — add logging with `structlog`
- Type coverage: fill in missing return types on FastAPI route handlers and React component props
- Naming: misleading variable names, single-letter variables outside of loops

---

## Phase 5 — Security Pass

A targeted check (not a full pentest).

- SQL injection: raw string interpolation into queries (even via SQLAlchemy `text()`)
- JWT: token expiry enforced, refresh token rotation, token stored safely (not localStorage for sensitive apps)
- Password: bcrypt rounds ≥ 12, no plaintext comparison fallback
- File uploads: MIME type validation, size limits, path traversal prevention
- CORS: tightened to known origins in production
- Dependency vulnerabilities: note any packages flagged in known CVE lists if visible

---

## Issue Tracker Format

For every issue found, record it as:

```
[SEVERITY] [LAYER] Short title
  File: path/to/file.py, line ~N
  Problem: what is wrong and why it matters
  Fix: exact change to make (show diff or replacement code)
```

Severity levels: **CRITICAL** (data loss / crash) · **HIGH** (incorrect behaviour) · **MEDIUM** (performance / maintainability) · **LOW** (style / minor)

---

## Output Format

After completing all phases, produce a report with these sections:

### 1. Summary
- One paragraph describing the project
- Counts: X critical, Y high, Z medium, W low issues found

### 2. Fixes Applied
List every change made (file, what changed, why).
For each fix, show a minimal before/after diff:
```
- old code
+ new code
```

### 3. Optimizations Applied
Same format as fixes, but for performance changes.

### 4. Remaining Recommendations
Issues that need human judgement (architecture decisions, migrations, secrets rotation) — listed with enough context to act on.

### 5. Health Score
Rate each layer 1–10 after fixes, with one sentence of justification:
- Frontend:
- Backend:
- Database:
- Infrastructure:

---

## Working Rules

1. **Fix, don't just report.** Apply every CRITICAL and HIGH fix directly. For MEDIUM/LOW, apply if safe; otherwise list in Recommendations.
2. **One file at a time.** Read a file fully before editing it. After editing, re-read to verify.
3. **Preserve intent.** Don't refactor logic you don't understand — flag it instead.
4. **Test awareness.** If pytest tests exist, check that fixes don't break test signatures. If httpx test clients are used, verify route names match.
5. **Show your work.** Every fix must reference the issue that caused it.

---

## Stack-Specific Reference

See `references/stack-notes.md` for version-specific gotchas for this project's exact stack
(React 19, FastAPI 0.115, SQLAlchemy 2.0 async, TanStack Query 5, Pydantic v2, etc.)