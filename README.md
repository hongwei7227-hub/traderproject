# Kairos Trader

A multi-tenant platform for running LLM agents over financial data.

One deployment serves many accounts. Each account brings its own model
preferences, its own credentials and its own spending limit, and none of them
can see or exhaust another's. The agent layer streams its reasoning as it
happens, so a turn that takes a minute shows its work for the whole minute
rather than going quiet and then arriving.

```bash
cd server
uv sync --extra dev
python scripts/demo.py --probe     # full stream, no database, no API key
```

The demo replays a scripted model. Everything else in the path is real — the
same middleware, the same repositories, the same SSE encoder that production
uses — so it exercises the wiring rather than a mock of it.

## Layout

Two deployables and the writing that explains them.

<pre>
<a href="server/">server/</a>          Python — the platform
<a href="web/">web/</a>             React — the interface
<a href="docs/">docs/</a>            architecture notes and the specifications
</pre>

## Architecture

Hexagonal, with the domain packaged by subject rather than by technology.

<pre>
<a href="server/kairos/">server/kairos/</a>
├── <a href="server/kairos/core/">core/</a>            domain — imports no framework, no database, no network
│   ├── <a href="server/kairos/core/tenancy/">tenancy/</a>       who a request belongs to
│   ├── <a href="server/kairos/core/catalog/">catalog/</a>       reachable models, and how one is chosen
│   ├── <a href="server/kairos/core/quota/">quota/</a>         token metering
│   ├── <a href="server/kairos/core/resilience/">resilience/</a>    failure memory
│   ├── <a href="server/kairos/core/reasoning/">reasoning/</a>     turn composition
│   ├── <a href="server/kairos/core/streaming/">streaming/</a>     the event protocol and its invariants
│   └── <a href="server/kairos/core/tools/">tools/</a>         tool registry and the execution sandbox
├── <a href="server/kairos/adapters/">adapters/</a>        outbound — implementations of the core's ports
│   ├── <a href="server/kairos/adapters/persistence/">persistence/</a>   ORM entities and scoped repositories
│   └── <a href="server/kairos/adapters/llm/">llm/</a>           wire formats, credentials, invocation
├── <a href="server/kairos/api/">api/</a>             inbound — HTTP, identity, SSE
├── <a href="server/kairos/runtime/">runtime/</a>         assembly — configuration, wiring, lifecycle
└── <a href="server/kairos/migrations/">migrations/</a>      schema history, shipped with the package it matches
</pre>

Dependencies point inward, always. That is not a convention here: it is
[asserted in the suite](server/tests/test_architecture.py), by reading imports
rather than following them, so a violation reports as a violation instead of as
an `ImportError` from something that happens not to be installed.

## Tenancy

Identity is ambient, not passed. A `TenantScope` is established once by the
middleware and read wherever it is needed:

```python
with scoped(TenantScope(tenant_id=..., user_id=..., roles=...)):
    workspaces = await WorkspaceRepository(session).list()
```

Reading the scope when none is set raises. It does not return `None` — that
would invite `if scope:` and a silent unscoped query. An absent scope is a
routing bug, and it surfaces as one.

Isolation lives in the repository base class rather than in the endpoints.
Every query originates from a statement that is already filtered by tenant, and
the only way around it is a method named `_unscoped_escape_hatch` that demands a
written justification. Writes are stamped from the scope instead of taking a
tenant argument, because a caller that can name the tenant it writes to can name
the wrong one. Cache keys lead with the tenant, so a check missed upstream
produces a miss rather than someone else's data.

## Model selection

Per request, per role. A single turn uses several models: the primary does the
reasoning, cheaper ones condense history and extract page text. Those jobs are
high-volume and low-judgment, and paying flagship rates for them is the easiest
way to waste money on a platform like this.

Precedence runs through a chain, one class per level:

```
explicit request → tenant preference → workspace default → system baseline
```

Tenant preferences are read from the database on every request, which is what
makes switching models take effect immediately rather than at the next deploy.
Each resolution reports which level decided it, so "why did my request go
there?" is answerable — and the settings page shows that answer on every row,
rather than a model name that could equally be a choice or a default.

An assignment is validated when it is made, not when it is used. A role
declares the capabilities it needs, and a model that lacks them is refused at
the point someone picks it — which turns a turn that dies partway through into
a rejected form.

Four provider wire formats are supported — `openai-chat`,
`openai-responses`, `anthropic-messages` and `gemini-generate`. A model
descriptor names its format; nothing above the adapter layer knows which one is
in use.

## Quota and degradation

Agent turns differ in cost by orders of magnitude, so counting requests does not
bound spending. The meter counts tokens, which forces a two-phase protocol:
reserve pessimistically before the work, settle to actual after it. In-flight
reservations count against the allowance, which is what stops a burst of
concurrent requests from collectively overrunning.

A provider that is failing is remembered rather than rediscovered. The circuit
breaker is keyed by `(tenant, provider)`: global keying would let one tenant's
exhausted quota cut everyone else off from a provider that is working fine for
them.

## Streaming

The turn is pushed as it is produced, not assembled and then replayed. The
protocol carries three ordering guarantees, and the suite holds them:

- metadata arrives before any content
- exactly one terminal event, whether the turn succeeded, failed or was cancelled
- sequence numbers are contiguous, so a client can tell a gap from a pause

Back-pressure drops content events and never structural ones — a slow reader
loses tokens, not the frame that tells it the turn ended. Abandoning the stream
cancels the turn behind it rather than leaving it running for nobody.

On the client, reconnection resumes from a cursor. Sub-task events do not
advance the shared cursor, replay never writes it, and a generation guard
discards a superseded attempt's frames, so a reconnect during a reconnect does
not interleave two histories.

## Frontend

React 19, Vite 7, TanStack Query 5, Tailwind 3, strict TypeScript.

The stream is read with `fetch` and `ReadableStream` rather than `EventSource`,
which cannot send an `Authorization` header. Server payloads are narrowed field
by field on arrival — there is no `as` cast across the network boundary, so a
backend that changes shape fails where it arrives instead of three components
later.

```bash
cd web
pnpm install
pnpm dev          # proxies /api/v1 to 127.0.0.1:8000
```

## Status

559 backend tests and 117 frontend tests, no external services required.

| Area | State |
|---|---|
| Tenancy — ambient scope, isolation guarantees | Done |
| Catalog — descriptors, registry, resolution chain | Done |
| Quota — two-phase reservation and settlement | Done |
| Resilience — circuit breaking, fallback planning | Done |
| Reasoning — declarative pipeline assembly | Done |
| Streaming — event protocol, SSE bridge, replay | Done |
| Persistence — entities, scoped repositories, migrations | Done |
| Identity — verification, tenant middleware | Done |
| LLM adapters — four wire formats, invocation | Done |
| Tools — registry, sandboxed execution | Done |
| Frontend — chat, threads, streaming client | Done |
| Frontend — settings: model assignments, usage | Done |
| Frontend — market, dashboard | Not started |
| Credential decryption | Deliberately unwired — see below |

Credential decryption raises `NotImplementedError` rather than returning
plaintext. Wiring key management should be an obvious missing step, not
something that appears to work until someone reads the storage.

The Java services this platform calls for order execution, analyst data and
payments are separate work and live outside this repository.

## Development

```bash
cd server
uv sync --extra dev
pytest                 # 559 tests, in-memory SQLite, no services needed
ruff check kairos/
mypy kairos/
alembic upgrade head   # only when running against a real database

cd ../web
pnpm test
pnpm typecheck
pnpm lint              # type-aware rules only; formatting is not linted
```

The suite runs against in-memory SQLite. Persistence entities use portable
column types for that reason — testing tenant isolation should not require
standing up a database.

Design notes are in [docs/architecture.md](docs/architecture.md); the
specifications the implementation was written against are in
[docs/specs/](docs/specs/).
