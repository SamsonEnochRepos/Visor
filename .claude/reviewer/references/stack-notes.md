# Stack-Specific Gotchas

Quick reference for version-specific pitfalls in this project's stack.
Read this when you encounter an unfamiliar pattern or a suspiciously "fine" piece of code.

---

## React 19

- `use()` hook is now stable — if you see manual promise unwrapping in components, suggest `use()`
- `ref` is now a prop, not `forwardRef` — old `forwardRef` wrappers still work but are deprecated
- `useOptimistic` is stable — missing from mutation flows that update UI before server confirmation
- Server Components don't apply here (Vite SPA), so ignore RSC-related patterns

## TypeScript 5.9

- `using` / `await using` (Explicit Resource Management) available — suggest for DB handles or cleanup-heavy resources
- `exactOptionalPropertyTypes` may be on — `undefined` ≠ missing key, check tsconfig
- Decorator metadata requires `experimentalDecorators: true` — check if shadcn or class-based libs need it

## Vite 7

- `import.meta.env` for env vars — never `process.env` in frontend code
- Top-level `await` is supported — no need for IIFE wrappers
- Check `build.rollupOptions.output.manualChunks` — missing chunking means one giant bundle
- `vite-plugin-checker` for in-build TypeScript errors; if absent, TS errors may be silent during `vite build`

## TanStack Query 5

- `useQuery({ queryKey, queryFn })` — `queryKey` must include all variables used inside `queryFn`
- `keepPreviousData` renamed to `placeholderData: keepPreviousData` (import from `@tanstack/react-query`)
- `onSuccess` / `onError` callbacks removed from `useQuery` — move to `useEffect` watching `data`/`error`
- `useMutation` `onSuccess` still works but prefer `mutateAsync` + try/catch for predictable flow
- Default `staleTime: 0` causes a refetch on every mount — set `staleTime: 60_000` for stable data

## React Router 7

- Uses `HashRouter` here (no server rewrite rules needed) — confirm `<HashRouter>` at root, not `<BrowserRouter>`
- `loader` functions and `action` functions are v6.4+ data API — check if used; if not, data fetching belongs in TanStack Query
- Missing `<Outlet />` in layout routes causes silent blank renders
- `useNavigate` inside effects needs a ref guard to prevent navigation after unmount

## Axios 1

- Always set `timeout` (e.g. 10000ms) — default is no timeout, causing hanging requests
- Interceptors: ensure 401 refresh logic doesn't create an infinite loop (guard with a `_retry` flag)
- `axios.create()` instances don't inherit global interceptors — check both global and instance interceptors
- `params` serialization: arrays default to `a[]=1&a[]=2`; use `paramsSerializer` if the backend expects `a=1&a=2`

## FastAPI 0.115

- `async def` route + sync DB call = blocking the event loop — use `asyncio.get_event_loop().run_in_executor()`
- `Depends()` with async generators: `yield` once, cleanup after yield runs after response is sent
- `response_model_exclude_unset=True` prevents leaking default None fields
- Background tasks (`BackgroundTasks`) are fire-and-forget — errors are swallowed unless you add a try/except inside the task
- `status_code=204` must return `None` — returning a body causes a serialization error

## Pydantic v2

| v1 pattern | v2 equivalent |
|---|---|
| `@validator('field')` | `@field_validator('field')` |
| `class Config: orm_mode = True` | `model_config = ConfigDict(from_attributes=True)` |
| `__fields__` | `model_fields` |
| `.dict()` | `.model_dump()` |
| `.json()` | `.model_dump_json()` |
| `@root_validator` | `@model_validator(mode='before'/'after')` |

- `pydantic-settings`: `BaseSettings` is now in `pydantic_settings`, not `pydantic`

## SQLAlchemy 2.0 async

- `async_session` must be used as an async context manager: `async with session_factory() as session`
- `session.execute(select(Model))` returns `Result` — call `.scalars().all()` to get ORM objects
- `session.add()` is sync; `await session.commit()` and `await session.refresh(obj)` are async
- Lazy loading raises `MissingGreenlet` in async context — always use `selectinload` or `joinedload` eagerly
- `relationship(..., lazy="selectin")` auto-loads in async; avoid `lazy="dynamic"` entirely

## Alembic 1.15

- `alembic revision --autogenerate` misses: custom types, server defaults, check constraints, index names
- Always review generated migration before applying — autogenerate is a starting point, not final truth
- Multiple heads: run `alembic heads` and merge with `alembic merge heads` before deploying
- `op.execute()` with raw SQL must handle both upgrade and downgrade

## asyncpg / psycopg2

- asyncpg uses `$1, $2` placeholders, not `%s` — mixing them causes silent failures
- asyncpg connections are not thread-safe — never share across coroutines without a pool
- psycopg2 is for Alembic only (sync context) — don't import it inside FastAPI route handlers

## APScheduler 3.11

- `add_job()` called at module level + uvicorn reload = duplicate jobs — guard with `if not scheduler.get_job(id)`
- `coalesce=True` prevents job pile-up if execution falls behind
- Always set `misfire_grace_time` so missed jobs don't silently skip
- Errors inside jobs are caught by APScheduler and logged — add an explicit error listener:
  ```python
  scheduler.add_listener(my_error_handler, EVENT_JOB_ERROR)
  ```

## python-jose JWT

- Always pin `algorithms=["HS256"]` (or RS256) — omitting allows algorithm confusion attacks
- Check `exp`, `iat`, and `nbf` claims — python-jose verifies `exp` by default but not `nbf`
- Prefer `RS256` with asymmetric keys for multi-service architectures

## passlib + bcrypt

- `bcrypt` rounds should be ≥ 12 (12 = ~250ms on modern hardware)
- `CryptContext(schemes=["bcrypt"], deprecated="auto")` handles old hash upgrades on login
- Never log or print the raw password — check structlog `bind()` calls for accidental inclusion

## structlog 25

- Configure once at startup with `structlog.configure(...)` — don't call it per-request
- Use `structlog.get_logger()` at module level, not inside functions (cheap and safe)
- In async contexts, `structlog.contextvars.bind_contextvars(request_id=...)` for per-request context

## ReportLab 4.4 / OpenPyXL 3.1

- Both are sync and CPU-heavy — wrap in `asyncio.get_event_loop().run_in_executor(None, fn)` inside async routes
- ReportLab: `SimpleDocTemplate` writes to a `BytesIO` — return as `StreamingResponse` in FastAPI
- OpenPyXL: `load_workbook(data_only=True)` to read computed cell values, not formulas

## PostgreSQL 18

- `JSONB` operators (`->>`, `@>`) are not typed by SQLAlchemy — use `cast()` explicitly
- `EXPLAIN ANALYZE` output in logs is your friend for slow queries — add `echo=True` temporarily
- Connection pool: set `pool_size=10, max_overflow=20` for typical web workloads; tune with `pg_stat_activity`