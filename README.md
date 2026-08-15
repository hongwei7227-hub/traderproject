# Kairos Trader

A multi-tenant platform for running LLM agents over financial data, and placing
the trades they argue for.

One deployment serves many accounts. Each account brings its own model
preferences, its own credentials and its own spending limit, and none of them
can see or exhaust another's. The agent layer streams its reasoning as it
happens, so a turn that takes a minute shows its work for the whole minute
rather than going quiet and then arriving.

```bash
cd server
uv sync --extra dev
python scripts/demo.py            # everything, no database, no API key, no Java
python scripts/demo.py --probe    # print a full turn's stream and exit
```

The demo replays a scripted model and stands in for the Java services.
Everything else in the path is real — the same middleware, the same
repositories, the same risk envelope, the same SSE encoder — so it exercises
the wiring rather than a mock of it. Orders placed against it move from
accepted to partially filled to filled, because the branches most likely to be
wrong are the ones a demo usually skips.

## Layout

Three deployables and the writing that explains them.

<pre>
<a href="server/">server/</a>          Python — the platform
<a href="web/">web/</a>             React — the interface
<a href="java/">java/</a>            Spring Boot — identity, execution, analyst data, billing
<a href="docs/">docs/</a>            architecture notes and the specifications
</pre>

The Java services own capabilities the platform does not implement itself. They
are reached over HTTP or a message queue rather than linked in, so one being
down degrades a page instead of taking the platform with it. See
[java/README.md](java/README.md) for their contracts.

| Service | Port | Owns |
|---|---|---|
| [login](java/login/) | 8081 | Identity: passwords, tokens, sessions |
| [recharge](java/recharge/) | 8082 | Membership, top-ups, payment |
| [execution-worker](java/execution-worker/) | 8090 | Broker adapter, order state machine, fills |
| [analyst-service](java/analyst-service/) | 8091 | Analyst ratings, two-level cache |

## Architecture

Hexagonal, with the domain packaged by subject rather than by technology.

<pre>
<a href="server/kairos/">server/kairos/</a>
├── <a href="server/kairos/core/">core/</a>            domain — imports no framework, no database, no network
│   ├── <a href="server/kairos/core/tenancy/">tenancy/</a>       who a request belongs to
│   ├── <a href="server/kairos/core/identity/">identity/</a>      what a verified caller looks like
│   ├── <a href="server/kairos/core/catalog/">catalog/</a>       reachable models, and how one is chosen
│   ├── <a href="server/kairos/core/quota/">quota/</a>         token metering
│   ├── <a href="server/kairos/core/resilience/">resilience/</a>    failure memory
│   ├── <a href="server/kairos/core/reasoning/">reasoning/</a>     turn composition
│   ├── <a href="server/kairos/core/streaming/">streaming/</a>     the event protocol and its invariants
│   ├── <a href="server/kairos/core/tools/">tools/</a>         tool registry and the execution sandbox
│   └── <a href="server/kairos/core/trading/">trading/</a>       proposals, orders, and the risk envelope
├── <a href="server/kairos/adapters/">adapters/</a>        outbound — implementations of the core's ports
│   ├── <a href="server/kairos/adapters/persistence/">persistence/</a>   ORM entities and scoped repositories
│   ├── <a href="server/kairos/adapters/llm/">llm/</a>           wire formats, credentials, invocation
│   ├── <a href="server/kairos/adapters/identity/">identity/</a>      the login service's sessions, read directly
│   ├── <a href="server/kairos/adapters/trading/">trading/</a>       the order outbox, and the worker's orders read back
│   └── <a href="server/kairos/adapters/services/">services/</a>      HTTP clients for the analyst and billing services
├── <a href="server/kairos/api/">api/</a>             inbound — HTTP, identity, SSE
├── <a href="server/kairos/runtime/">runtime/</a>         assembly — configuration, wiring, lifecycle
└── <a href="server/kairos/migrations/">migrations/</a>      schema history, shipped with the package it matches
</pre>

Dependencies point inward, always. That is not a convention here: it is
[asserted in the suite](server/tests/test_architecture.py), by reading imports
rather than following them, so a violation reports as a violation instead of as
an `ImportError` from something that happens not to be installed.

## Identity

The login service owns it. It checks the password, mints an opaque token and
writes the user behind it into Redis, sliding the expiry on each use. This
platform accepts the same token by reading the same key.

Reading rather than calling is deliberate: the alternative is an HTTP round
trip to a service that would then do exactly this lookup, on every request —
including every frame of a stream. The key format is configuration here rather
than a constant, so when it changes, one setting moves and the failure is a
clean "not logged in" rather than a subtle mismatch.

The expiry is refreshed here too. A session that only slid when its owner
happened to hit the login service would lapse mid-conversation.

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

## Trading

The platform proposes; the execution worker executes. Nothing here talks to a
broker, which is why placing an order cannot fail because one is unreachable.

A proposal is committed to an outbox table in the same transaction as the
request that produced it, and a relay drains that table to the worker's queue.
Publishing inside the request would make an order fail whenever the broker
happened to be down — and, worse, could publish an instruction whose
surrounding transaction then rolled back, leaving the worker holding an order
the platform has no record of.

The relay runs outside any request, so an order survives the broker being down,
the relay restarting, and the web process being replaced under it. A row is
marked sent only after the publish returns — a crash mid-sweep re-delivers
rather than drops, which is safe precisely because the worker's idempotency key
was generated before the proposal was queued.

```bash
python scripts/relay.py             # drain to RocketMQ, until stopped
python scripts/relay.py --dry-run   # log what would be sent, send nothing
python scripts/relay.py --once      # one sweep, for a scheduler rather than a daemon
```

Coming back, the worker's `orders` table is read and never written. One writer,
because two is how a fill gets overwritten by a status that was already stale
when it was read. Positions are derived from those orders rather than stored,
because the orders are the record and a second copy can disagree with them.

The risk envelope is checked before the proposal is queued. The worker checks
it again and its refusal is the one that counts — but its refusal arrives
seconds later as a `DENIED` status attached to an order nobody wanted, and it
cannot say which limit was hit and by how much. Every breach is reported, not
just the first: reporting one turns correcting an order into a guessing game
where each fix reveals the next objection.

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

Pages: conversations, trading, membership, settings. The order ticket shows
every risk refusal with the numbers attached, and the analyst card says when
its service is unavailable rather than rendering an empty rating — which in a
card looks like coverage that says nothing.

```bash
cd web
pnpm install
pnpm dev          # proxies /api/v1 to 127.0.0.1:8000
```

## Status

745 backend tests and 140 frontend tests, no external services required.

| Area | State |
|---|---|
| Tenancy — ambient scope, isolation guarantees | Done |
| Identity — login-service sessions, tenant middleware | Done |
| Catalog — descriptors, registry, resolution chain | Done |
| Quota — two-phase reservation and settlement | Done |
| Resilience — circuit breaking, fallback planning | Done |
| Reasoning — declarative pipeline assembly | Done |
| Streaming — event protocol, SSE bridge, replay | Done |
| Persistence — entities, scoped repositories, migrations | Done |
| LLM adapters — four wire formats, invocation | Done |
| Tools — registry, sandboxed execution | Done |
| Trading — risk envelope, order outbox, orders and positions | Done |
| Outbox relay — the process that drains the queue | Done |
| Services — analyst and billing clients, per-tenant breaking | Done |
| Frontend — chat, threads, streaming client | Done |
| Frontend — sign-in, trading, membership, settings | Done |
| Market data — live quotes, WebSocket | Not started |
| Frontend — dashboard, market view | Not started |
| Credential decryption | Deliberately unwired — see below |

Credential decryption raises `NotImplementedError` rather than returning
plaintext. Wiring key management should be an obvious missing step, not
something that appears to work until someone reads the storage.

The relay is written and tested; the RocketMQ publisher inside it has not been
run against a live broker. Everything above it has: the queueing, the retry,
the give-up, the shutdown, and the re-delivery after a broker comes back are
all exercised, and `--dry-run` runs the whole path without one.

## Development

```bash
cd server
uv sync --extra dev
pytest                 # 745 tests, in-memory SQLite, no services needed
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
