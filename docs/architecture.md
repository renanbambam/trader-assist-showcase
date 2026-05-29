# Architecture — Oracle Trader Assist

## Context

Oracle Trader Assist supports intraday trading workflows by integrating four main concerns: real-time market data, AI-powered analysis, trade lifecycle management, and session replay. The system runs locally or on a private server and is accessed via a browser-based frontend.

The primary design goals were:
1. **Testability** — domain and application logic testable without Docker, without network, without environment variables
2. **Evolvability** — AI provider, database, and market data source all replaceable without touching business logic
3. **Reliability** — real-time WebSocket delivery to multiple clients without any single slow client causing degradation

---

## Clean Architecture

The codebase is organized into four layers. The dependency rule is enforced structurally: inner layers have no imports from outer layers, verified by import linting in CI.

```
Interface  →  Application  →  Domain
Infrastructure  →  Application  →  Domain
```

Infrastructure never imports from Interface (and vice versa). Both are peers that depend only on Application.

### Domain Layer

Pure Python. No FastAPI, no SQLAlchemy, no Pydantic v2 validators that carry framework state. Entities use dataclasses or plain Pydantic `BaseModel` with `model_config = ConfigDict(frozen=True)` for value objects.

Key design decision: value objects are immutable and identified by their values, not by an `id`. Entities have an `id` and are mutable through well-defined domain methods. This distinction is enforced in code, not just convention.

Domain services (e.g., `ChecklistValidator`) are stateless functions or classes that operate on domain objects — they contain logic that doesn't belong to a single entity but still lives in the domain layer.

### Application Layer

Use cases are thin orchestrators. A use case:
- Accepts a typed DTO (Input object)
- Calls domain services or repositories (via typed interfaces)
- Returns a typed DTO (Output object)
- Has no knowledge of HTTP, WebSocket, or database implementation details

Port interfaces (`TradeRepository`, `AIProvider`, `MarketDataProvider`) are abstract classes defined in the application layer. Infrastructure implements them. This is the Dependency Inversion Principle applied at the layer boundary.

### Infrastructure Layer

Each external concern is encapsulated in an adapter:

| Adapter | Interface it implements |
|---------|------------------------|
| `SQLAlchemyTradeRepository` | `TradeRepository` |
| `ClaudeAdapter` | `AIProvider` |
| `YFinanceMarketDataAdapter` | `MarketDataProvider` |
| `MT5MarketDataAdapter` | `MarketDataProvider` |
| `RedisContextRepository` | `ContextRepository` |

Swapping PostgreSQL for SQLite in tests is a one-line change in the `Settings` object passed to `create_app()`.

### Interface Layer

FastAPI routers are thin. Each endpoint:
1. Extracts and validates the request (Pydantic model)
2. Builds the input DTO
3. Calls the use case
4. Returns the output DTO

No business logic lives in routers. Route handlers don't call repositories directly — they always go through a use case.

---

## Concurrency Model

The system is fully async from the ASGI server (uvicorn) down to the database driver (asyncpg) and Redis client (aioredis).

**Blocking operations** that cannot be made async are wrapped in `asyncio.get_running_loop().run_in_executor(None, ...)`:
- Alembic migrations (synchronous, run once at startup)
- MetaTrader5 Python library (synchronous C extension)
- yfinance (synchronous HTTP via requests)
- APScheduler job execution callbacks

This keeps the event loop unblocked during I/O-heavy operations while maintaining a single async execution model throughout the rest of the stack.

---

## WebSocket Architecture

The WebSocket subsystem has three components:

**WebSocketGateway** — manages connections and subscriptions. Clients connect, receive a `system.connected` event with their `client_id`, and subscribe to channels (`market`, `analysis`, `ai_stream`, `trading`, `replay`, `system`). The gateway stores `client_id → WebSocket` and `client_id → set[channel]` mappings.

**EventBus (Redis)** — domain events are published to Redis pub/sub channels. The event structure is `{event: str, channel: str, payload: dict}`.

**EventBroadcaster** — a background asyncio task that subscribes to the Redis event bus and calls `gateway.broadcast_to_channel()` for each event. This decouples the producer (use case that publishes an event) from the consumer (WebSocket clients).

The fan-out in `broadcast_to_channel` uses `asyncio.gather(*sends, return_exceptions=True)`. Individual send failures are silently collected — a dead connection doesn't interrupt delivery to others.

**Cold-start snapshots**: when a client subscribes to a channel, the gateway optionally calls a `snapshot_provider(channel)` to deliver the last known state immediately. This prevents the client from seeing an empty UI until the next event arrives.

---

## AI Integration

The `ClaudeAdapter` wraps the Anthropic SDK and exposes two methods:

- `complete(prompt, system)` → `str` — single-turn analysis, returns the full response
- `stream(prompt, system)` → `AsyncGenerator[str, None]` — streaming analysis, yields text chunks as they arrive

Prompt caching is applied to the system prompt via `cache_control: {"type": "ephemeral"}`. On the first request, the system prompt is cached on Anthropic's infrastructure. Subsequent requests within the cache window (5 minutes) reuse the prefix, cutting the token count processed by ~80%.

The adapter is injected into use cases via the `AIProvider` interface — the use case has no knowledge of which model or SDK is being used.

---

## Scheduling

`APScheduler` runs three scheduled jobs:

| Job | Schedule | Description |
|-----|----------|-------------|
| `morning_briefing` | Mon–Fri 07:00 | Generate and deliver briefing via Telegram |
| `weekly_summary` | Mon 08:00 | Weekly performance summary |
| `news_refresh` | Every 30 min | Refresh market context cache |

The scheduler uses `MemoryJobStore` rather than `RedisJobStore`. The reason: `RedisJobStore` pickles the job's `kwargs` dict, which includes a SQLAlchemy async engine. Async engines are not picklable. `MemoryJobStore` avoids this entirely at the cost of not persisting jobs across restarts — acceptable for cron-style jobs that re-register at startup anyway.

---

## Testing Strategy

**Unit tests** (`tests/unit/`) cover domain logic and application use cases. Dependencies are provided as simple in-memory fakes (not mocks) — a `FakeTradeRepository(list)` that stores entities in a dict. No framework, no I/O, fast.

**Integration tests** (`tests/integration/`) spin up real PostgreSQL and Redis containers via Testcontainers. The app is created with `create_app(settings=test_settings)` where `test_settings` points to the container URLs. No environment variables are monkeypatched. Tests make real HTTP requests via `httpx.AsyncClient` with the ASGI transport.

The `ENVIRONMENT="test"` flag in settings disables the broadcaster task and APScheduler, which both require a live Redis connection and would cause teardown timeouts in the test suite.

---

## Deployment

The system is deployed as a single Docker container (uvicorn + FastAPI) with PostgreSQL and Redis as external services managed via docker-compose. The frontend is served as static files by FastAPI's `StaticFiles` mount, so no separate web server is needed.

The Dockerfile uses a multi-stage build: a build stage installs dependencies into a venv, and the final stage copies only the venv and source, keeping the image under 400MB.
