# TubeAgent Web + API Roadmap

A pragmatic, production‑grade plan to deliver a FastAPI backend and a professional React + Tailwind chat UI. Use this as an executable TODO list. Tasks are grouped by milestones to enable iterative delivery.

## Milestones

- [ ] M0 — Project scaffolding (FastAPI-only API + React tooling)
- [ ] M1 — Simple chat API + WebSocket streaming (no DB)
- [ ] M2 — Production-grade chat UI (elegant, polished UX)
- [ ] M3 — UI polish, performance, and a11y excellence
- [ ] M4 — Observability and deployment (minimal backend ops)

---

## Architecture Overview

- Backend: FastAPI (ASGI) only, Pydantic v2, WebSocket streaming. No DB, Redis, or auth initially; use in-memory stores and simple config. Keep the surface small and testable.
- Agent layer: integrate existing `src/agent` components (planner, controller, toolkit) behind a thin service adapter.
- Frontend: React 18 + TypeScript (Vite), Tailwind CSS, shadcn UI (Radix), TanStack Query, Zustand store, WebSocket client for live tokens.
- Communication: REST for sessions/messages (ephemeral in-memory), WebSocket for streamed assistant tokens and events.
- Packaging: Docker + docker‑compose for local dev (api + web), CI for lint/tests; deploy API to Fly.io/Render and web to Vercel/Netlify.

---

## Conventions

- Backend code in `app/` (FastAPI project root)
- Frontend code in `web/` (Vite React app)
- Shared developer docs in `docs/`
- Python 3.11+, Node 20+
- Use `ruff`, `black`, `mypy` (strict-ish) and `pytest`
- Use `eslint`, `prettier`, `vitest`/`jest`, `@testing-library/react`, `playwright`

---

## M0 — Scaffolding & Tooling

- [ ] Backend: initialize FastAPI project structure in `app/`
  - [ ] `app/main.py` (FastAPI instance, CORS, lifespan)
  - [ ] `app/api/routes` (health, sessions, messages)
  - [ ] `app/core/config.py` (Pydantic settings from env)
  - [ ] `app/core/logging.py` (structlog/loguru)
  - [ ] `app/services/agent.py` (adapter into `src/agent/*`)
  - [ ] `app/sockets/` (WebSocket manager, events)
  - [ ] `app/schemas/` (pydantic models)
- [ ] Backend dependencies
  - [ ] `fastapi`, `uvicorn[standard]`, `pydantic-settings`
  - [ ] `structlog` or `loguru`, `httpx`
- [ ] Backend tooling
  - [ ] `ruff`, `black`, `mypy`, `pytest`, `pytest-asyncio`, `httpx`
  - [ ] Makefile targets: `make dev`, `make test`, `make lint`, `make fmt`
- [ ] Frontend: initialize Vite + React + TS in `web/`
  - [ ] Add Tailwind CSS + PostCSS config
  - [ ] Add shadcn UI (Radix primitives) + `lucide-react`
  - [ ] Add TanStack Query, Zustand, React Router
  - [ ] Add `react-markdown` + code block highlighting (`shiki` or `prism`)
  - [ ] Project aliases, strict TS config
- [ ] Frontend tooling
  - [ ] `eslint` (typescript, react), `prettier`, `vitest`, `@testing-library/react`
  - [ ] Base `playwright` setup for smoke E2E
- [ ] Dev experience
  - [ ] `docker-compose.yml` (api, web)
  - [ ] `.env.example` for both api and web
  - [ ] Pre-commit hooks (ruff/black/eslint/prettier)

---

## M1 — Simple Chat API + Streaming (FastAPI-only)

- [ ] Health & metadata
  - [ ] `GET /health` (readyz/livez)
  - [ ] `GET /meta` (build/version, model options)
- [ ] Sessions
  - [ ] `POST /sessions` (create; in-memory store)
  - [ ] `GET /sessions` (list; in-memory)
  - [ ] `GET /sessions/{id}` (get; in-memory)
  - [ ] `DELETE /sessions/{id}` (delete from memory)
- [ ] Messages
  - [ ] `GET /sessions/{id}/messages` (basic pagination over memory)
  - [ ] `POST /sessions/{id}/messages` (new user message -> trigger agent pipeline)
  - [ ] Support roles: `user`, `assistant`, `tool`, `system`
 - [ ] Streaming
   - [ ] `WS /ws/chat/{session_id}` (bidirectional)
   - [ ] Event types: `connected`, `token`, `message_complete`, `error`, `typing`, `tool_call`, `tool_result`
   - [ ] Backpressure & heartbeat/ping
   - [ ] Graceful reconnect strategy
 - [ ] Cleanup & lifecycle
   - [ ] On user session end/delete, purge runtime directory: `/Users/khatri/TubeAgent/runtime` (ensure path guard; ignore if missing)
   - [ ] If using Google Gemini uploads, delete uploaded files via `client.files.delete(name=myfile.name)` for each file; ignore 404s
 - [ ] Agent service
   - [ ] Wrap `src/agent/core/controller.py` as `AgentService.respond()`
   - [ ] Token streaming hook from LLM client to WS
   - [ ] Tool execution surfaced as events with partial updates
- [ ] Validation & errors
  - [ ] Pydantic schemas for requests/responses
  - [ ] Global exception handlers, error codes, 422 normalization
- [ ] CORS & CSRF
  - [ ] Configure CORS for `web` origin
  - [ ] CSRF not required for WS; document security model

---

## M2 — Production-Grade Chat UI (elegant)

- [ ] Base layout
  - [ ] Responsive split: left sidebar (sessions), main chat, right drawer (settings)
  - [ ] Design system: shadcn tokens, Tailwind themes, semantic colors
  - [ ] Dark/light themes, system preference, smooth transitions
- [ ] Session list (sidebar)
  - [ ] Create, rename, delete, search, pin, reorder via drag & drop
  - [ ] Recent message preview, unread indicator, keyboard nav (j/k)
- [ ] Chat surface
  - [ ] Message bubbles with roles, timestamp, avatar, status ticks (sending/streaming/sent)
  - [ ] Markdown render (code blocks, tables, inline math via KaTeX, images)
  - [ ] Syntax highlighting (shiki) + copy code button + language badges
  - [ ] Quoting + inline replies; linkable message anchors
  - [ ] Streaming caret + typing indicator; auto-scroll with anchor retention
  - [ ] Message actions: retry, edit-and-resend, delete, copy, quote
  - [ ] Attachment pill (files), display preview if image/audio/video
- [ ] Composer (bottom input)
  - [ ] Multiline with `Enter` to send / `Shift+Enter` newline
  - [ ] Toolbar: upload, stop generation, system prompt presets, model selector
  - [ ] Prompt templates / quick suggestions
  - [ ] Disabled and progress states
  - [ ] Expanding textarea with character counter and token estimate (optional)
- [ ] Settings (right drawer)
  - [ ] Model + temperature + system prompt per session
  - [ ] Token/usage counter (if available)
  - [ ] Keyboard shortcuts reference
- [ ] Connectivity
  - [ ] WebSocket client with auto-reconnect + backoff
  - [ ] Toasts for disconnect/reconnect/errors
- [ ] State & data
  - [ ] TanStack Query for REST, Zustand for UI/session state
  - [ ] Cache strategy for recent messages per session (memory + storage)
- [ ] Polishing & accessibility
  - [ ] Focus management, ARIA roles, color contrast; axe checks in CI
  - [ ] Motion: tasteful micro-animations for list and message enter/exit
  - [ ] High-quality empty states and loading skeletons
  - [ ] Responsive typography scale and tight spacing rhythm
  - [ ] Performance: virtualized message list for long histories

---

## M3 — UI Polish, Performance, A11y

- [ ] Performance
  - [ ] Virtualize messages; incremental rendering for markdown/code blocks
  - [ ] Code-split routes and heavy components; prefetch critical assets
  - [ ] Debounce WS-driven renders; minimize re-renders via memoization
- [ ] Keyboard and power-user features
  - [ ] Global shortcuts (new chat, focus input, stop, send)
  - [ ] Command palette for common actions
- [ ] Theming and brand
  - [ ] Tune semantic color tokens; audit contrast (WCAG AA/AAA)
  - [ ] Iconography and micro-interactions polish
- [ ] QA and resilience
  - [ ] Offline read-only mode for recent sessions (localStorage)
  - [ ] Graceful error surfaces, retry UI, reconnection awareness
  - [ ] Visual regression tests for chat elements (Storybook or Chromatic optional)

---

## M4 — Observability, Hardening, Deployment

- [ ] Observability
  - [ ] Structured logs (request IDs, session IDs)
  - [ ] Error monitoring (Sentry) — optional but recommended
- [ ] Security & hardening
  - [ ] Input size limits; safe markdown rendering; attachment validation
  - [ ] CORS allowlist for `web` origin
- [ ] CI/CD
  - [ ] GitHub Actions: lint + test + typecheck (api/web)
  - [ ] Build Docker images, push to registry
- [ ] Deployment
  - [ ] Compose production manifests (Fly.io/Render API); Vercel/Netlify for `web`
  - [ ] TLS, custom domain, CDN cache for static assets
  - [ ] Scalability plan: API replicas and WS sticky sessions

---

## API Sketch

- `GET /health` -> { status: "ok" }
- `GET /meta` -> { version, models, limits }
- `POST /sessions` -> { id, title, created_at }
- `GET /sessions` -> [ Session ]
- `GET /sessions/{id}` -> Session
- `DELETE /sessions/{id}` -> { ok: true }
- `GET /sessions/{id}/messages?cursor=` -> { items: [Message], nextCursor }
- `POST /sessions/{id}/messages` -> { messageId } (and triggers streaming on WS)
- `WS /ws/chat/{session_id}`
  - inbound: { type: "user_message", text, attachments? }
  - outbound: token | message_complete | tool_call | tool_result | error | typing

---

## Backend Structure (proposed)

```
app/
  main.py
  core/
    config.py
    logging.py
  api/
    routes/
      health.py
      sessions.py
      messages.py
  sockets/
    manager.py
    events.py
  services/
    agent.py
    sessions.py
    messages.py
  schemas/
    session.py
    message.py
```

---

## Frontend Structure (proposed)

```
web/
  index.html
  src/
    main.tsx
    app/
      routes.tsx
      providers.tsx
    components/
      chat/
        Chat.tsx
        MessageItem.tsx
        Composer.tsx
        SessionSidebar.tsx
        SettingsDrawer.tsx
      ui/ (shadcn generated)
    pages/
      ChatPage.tsx
      SettingsPage.tsx
    api/
      client.ts (REST + WS helpers)
      sessions.ts
      messages.ts
    store/
      useSessionStore.ts
      useUIStore.ts
    lib/
      markdown.ts
      formatting.ts
    styles/
      globals.css
```

---

## Integration with `src/agent`

- [ ] Define `AgentService.respond(message, session_ctx) -> AsyncGenerator[token|event]`
- [ ] Map existing controller/planner/toolkit abstractions to service layer
- [ ] Add adapters for tool events to WS events (`tool_call`, `tool_result`)
- [ ] Provide streaming callbacks from LLM client to WS manager
- [ ] Add configuration bridging via `app/core/config.py`

---

## Testing Strategy

- [ ] Backend
  - [ ] Unit: services (agent/session/message), schemas
  - [ ] API: `httpx.AsyncClient` + `pytest-asyncio`
  - [ ] WS: streaming contract with test client (token -> complete)
- [ ] Frontend
  - [ ] Unit: components (message rendering, composer)
  - [ ] Integration: WS client behavior (mock server)
  - [ ] E2E: Playwright basic flows (create session, send message, stream)

---

## Developer Workflows

- [ ] `make dev` — run API (reload) + web via docker compose
- [ ] `make test` — run backend tests; `pnpm test` in `web`
- [ ] `make fmt && make lint` — enforce style

---

## Backlog / Nice‑to‑haves

- [ ] Persistence: PostgreSQL + SQLAlchemy + Alembic
- [ ] Authentication (JWT or OAuth) + protected routes
- [ ] Background jobs (Redis + worker) for long-running tasks
- [ ] SSE fallback to WS
- [ ] Multi-provider LLM routing (OpenAI, local, etc.)
- [ ] Prompt library with versioning
- [ ] Inline image/audio previews and multimodal inputs
- [ ] Slash commands in composer (`/summarize`, `/search`)
- [ ] Shareable read-only session links with redaction
- [ ] Export chat as Markdown/HTML/PDF
- [ ] Offline support (PWA) for reading history

---

## Acceptance Criteria

- [ ] A user can create a session, send a message, and see streamed assistant responses in the UI with graceful reconnect.
- [ ] Sessions and messages work with an in-memory backend; persistence is optional (backlog).
- [ ] Errors surface as toasts; logs, traces, and metrics capture request IDs.
- [ ] CI passes lint, type checks, and tests; a deployment target is configured.

---

## Next Action (suggested)

- [ ] Proceed with M0: set up `app/` FastAPI scaffolding and `web/` Vite React app with Tailwind/shadcn, plus shared tooling and compose.
