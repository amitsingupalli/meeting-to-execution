# Meeting to Execution

Turn meeting transcripts into **approved, tracked execution** — with a durable decision memory,
two-stage conflict detection, and a human approval gate that cannot be bypassed.

This repository is the complete backend. The UI is intentionally not included; a detailed,
copy-paste prompt for building it lives in [`FRONTEND_PROMPT.md`](FRONTEND_PROMPT.md).

---

## Why this exists

Meeting notes usually fail in one of three ways:

1. **They forget.** Decisions from six weeks ago are invisible, so the same debate happens again.
2. **They contradict.** A new decision silently conflicts with an old one, and both look current.
3. **They don't execute.** Action items are buried in prose and never reach a tracker.

This system treats a transcript as an untrusted source that must be:

- parsed into evidence-backed decisions and action items,
- compared against durable decision memory,
- reviewed by a human with conflicts and warnings in view,
- and only then committed and posted to a tracker.

The approval gate is a **hard interrupt** in the graph, not a UI convention. No decision, task,
tracker issue, or reminder is written before an approval that matches the exact review packet.

---

## What it does

```text
transcript
   │
   ▼
┌────────────┐   ┌────────────┐   ┌────────────┐   ┌────────────┐
│  Extract   │──▶│ Normalize  │──▶│ Retrieve   │──▶│  Judge     │
│  (LLM or   │   │ owners,    │   │ candidates │   │ conflicts  │
│  rules)    │   │ deadlines, │   │ from memory│   │ (2-stage)  │
│            │   │ evidence   │   │ + vectors  │   │            │
└────────────┘   └────────────┘   └────────────┘   └─────┬──────┘
                                                        │
                                                        ▼
                                                ┌───────────────┐
                                                │ Review packet │
                                                │ + packet hash │
                                                └───────┬───────┘
                                                        │
                                   ┌────────────────────┴───────────────────┐
                                   │  HUMAN APPROVAL (LangGraph interrupt)  │
                                   │  approve / edit / reject every item    │
                                   └────────────────────┬───────────────────┘
                                                        │ approved
                                                        ▼
                        ┌───────────────┬───────────────┬────────────────┐
                        │ Commit memory │ Post tracker  │ Schedule       │
                        │ + supersede   │ (idempotent)  │ reminders      │
                        └───────────────┴───────────────┴────────────────┘
                                                        │
                                                        ▼
                                             weekly per-person digest
```

### Decision memory

Every approved decision is stored with a structured key (`subject`, `attribute`, `value`), the
verbatim source quote, speaker, meeting, date, confidence, and status (`active` / `superseded`).
New decisions are retrieved by a hybrid of structured subject matching and vector similarity
(`sqlite-vec`, with a portable JSON cosine fallback).

### Two-stage conflict detection

1. **Retrieval** finds the most similar active decisions from *other* meetings.
2. **Judging** classifies each pair as `contradicts`, `refines`, `duplicates`, or `unrelated`,
   with a rationale and confidence.

Similarity alone never creates a conflict. Same subject with a different attribute is explicitly
`unrelated`. Date/timeline changes are `refines`; a changed value for the same attribute is
`contradicts`; an identical value is `duplicates`.

The reviewer sees every non-trivial conflict and chooses to supersede, keep both, mark duplicate,
or reject the item.

### Execution

- Action items become tracker issues only after approval.
- Idempotency keys make posting safe to retry; the mock tracker and GitHub Issues both dedupe.
- Duplicate tasks (same dedupe key or ~90% similar title for the same owner) are skipped with a
  link to the original.
- Reminders are scheduled 48 hours before deadlines (configurable).
- A weekly digest is generated per owner from approved/posted tasks.
- Notifications are persisted and written to a local log by default.

---

## Quickstart

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra llm --extra dev
cp .env.example .env          # optional; defaults work offline
uv run mte demo               # end-to-end four-meeting demo
uv run mte eval               # golden-set evaluation
uv run pytest                 # test suite
uv run ruff check src tests   # lint
```

The demo and tests run fully offline with deterministic extraction and judging. No API key or
GitHub token is required.

### Run the API

```bash
uv run mte init               # create SQLite tables
uv run mte serve --reload     # http://127.0.0.1:8000/docs
```

### Seed the fixture meetings

```bash
uv run mte seed
```

This ingests and auto-approves the four synthetic meetings through the same pipeline the API uses.

---

## Demo: what to look for

`uv run mte demo` runs four meetings in chronological order:

| Meeting | What happens |
| --- | --- |
| Platform Planning (Nov 2) | Postgres, AWS, beta launch Dec 4; three action items |
| Platform Follow-up (Nov 9) | Redis for caching; duplicate beta-launch decision is detected; duplicate schema task is skipped |
| Architecture Review (Nov 16) | Postgres → MySQL contradiction; beta launch Dec 4 → Dec 18 refinement; duplicate AWS decision |
| Security & Launch Sync (Nov 20) | Auth0; Redis → database-backed cache contradiction; launch date duplicate |

Expected final state:

```text
4 meetings | 6 active decisions | 3 superseded | 9 tasks | 9 posted
```

The demo also:

- assigns an unowned authentication task to Elena during review,
- sends due reminders by advancing the clock,
- generates per-person weekly digests,
- and writes notifications to the database and `data/demo/notifications.log`.

---

## Configuration

Copy `.env.example` to `.env`. All values have offline-friendly defaults.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./data/mte.db` | SQLite database (SQLite only today) |
| `CHECKPOINT_DB` | `./data/checkpoints.db` | LangGraph durable checkpoint database |
| `VECTOR_BACKEND` | `local` | `local`/`sqlite-vec` or `json` cosine fallback |
| `LLM_API_KEY` | empty | Enables OpenAI-compatible extraction, judging, and digest prose |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible endpoint |
| `LLM_MODEL` | `gpt-4o-mini` | Chat model |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model when LLM is enabled |
| `TRACKER_BACKEND` | `mock` | `mock` or `github` |
| `GITHUB_TOKEN` / `GITHUB_REPO` | empty | Required for the GitHub tracker |
| `ENABLE_SCHEDULER` | `true` | APScheduler reminder/digest jobs |
| `REMINDER_HOURS_BEFORE` | `48` | Reminder lead time |
| `DIGEST_DAY_OF_WEEK` | `mon` | Weekly digest day |
| `DIGEST_HOUR` / `DIGEST_MINUTE` | `9` / `0` | Weekly digest time (UTC) |

When `LLM_API_KEY` is absent, a deterministic rule-based extractor and judge are used. When it is
present, the LLM path is tried first and falls back to rules on any error, so the pipeline stays
runnable.

---

## API at a glance

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/meetings` | Ingest a transcript and stop at approval |
| `GET` | `/api/meetings` | List meetings |
| `GET` | `/api/meetings/{id}` | Meeting detail |
| `GET` | `/api/reviews` | Review queue, filterable by status |
| `GET` | `/api/reviews/{thread_id}` | Current review packet |
| `POST` | `/api/reviews/{thread_id}` | Approve, edit, or reject the packet |
| `GET` | `/api/decisions` | Decision memory, filterable by status/meeting |
| `GET` | `/api/decisions/{id}` | Decision detail |
| `GET` | `/api/conflicts` | Persisted conflicts, filterable |
| `GET` | `/api/tasks` | Tasks, filterable by status/owner |
| `GET` | `/api/tasks/{id}` | Task detail |
| `GET` | `/api/reminders` | Scheduled/sent reminders |
| `GET` | `/api/digests` | Stored weekly digests |
| `GET` | `/api/digests/preview` | Preview a digest for an owner/week |
| `GET` | `/api/notifications` | Notification log |
| `POST` | `/api/jobs/reminders/run` | Send due reminders now |
| `POST` | `/api/jobs/digest/run` | Generate weekly digests now |
| `GET` | `/api/health` | Health and backend configuration |
| `GET` | `/api/stats` | Dashboard counters |

Full request/response examples: [`docs/API.md`](docs/API.md).

---

## Safety properties

- **No pre-approval writes.** The graph reaches an `interrupt` before `commit`; memory, tracker,
  and reminders are untouched until resume.
- **Packet-bound approval.** Every packet has a SHA-256 hash over its canonical JSON. Approval must
  include that hash. A changed or stale packet is rejected.
- **Complete review.** Approval requires a verdict for every decision and every non-skipped task.
- **Owner validation.** Edited owners must be attendees of the meeting.
- **Evidence retention.** Each item keeps the exact transcript quote; missing quotes are flagged and
  confidence is capped.
- **Idempotent posting.** Tracker writes are keyed and safe to retry.
- **Atomic commit.** Decisions, supersessions, tasks, conflicts, and review status commit in one
  database transaction before vector upserts.

---

## Evaluation

`uv run mte eval` runs the deterministic pipeline against the four fixtures and a golden set:

```text
extraction precision  1.0
extraction recall     1.0
conflict precision    1.0
conflict recall       1.0
22 expected items     22 matched
6 expected conflicts  6 matched
```

The eval exercises ingest → extraction → normalization → retrieval → judging → approval → commit →
posting, not just isolated functions. The golden set lives in [`evals/golden.json`](evals/golden.json).

Tests cover:

- transcript parsing and evidence quotes,
- hedged language rejection,
- owner/deadline resolution anchored to the meeting date,
- every conflict relation,
- the approval gate, rejection, stale hashes, and partial reviews,
- supersession and duplicate suppression,
- tracker idempotency,
- the FastAPI flow, job triggers, and error codes.

---

## Project layout

```text
src/meeting_to_execution/
  api.py            FastAPI app and endpoints
  graph.py          LangGraph pipeline, interrupt, commit, posting
  extraction.py     transcript parser, rule-based and LLM extractors
  normalization.py  owner mapping, date resolution, task drafts
  judge.py          two-stage conflict classification
  memory.py         decision/task/review/reminder/digest repository
  models.py         SQLAlchemy tables
  vector_index.py   sqlite-vec index and JSON cosine fallback
  tracker.py        mock and GitHub Issues integrations
  reminders.py      deadline reminders
  digest.py         weekly per-person digests
  scheduler.py      APScheduler jobs
  evals.py          golden-set harness
  cli.py            init / serve / demo / seed / eval
fixtures/meetings/  four synthetic transcripts
evals/golden.json   expected extraction and conflict outcomes
tests/              pytest suite
docs/               architecture, API, demo notes
```

More detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Production notes

This is a complete, runnable reference implementation, not a multi-tenant SaaS. Before production:

- move from SQLite to Postgres (the repository layer is the seam),
- put auth and workspace scoping in front of the API,
- run the scheduler as a single dedicated worker,
- replace the log notifier with email/Slack,
- use a real embedding model and pin model versions,
- add rate limits, audit export, and retention policies,
- and review the GitHub tracker's search-based dedupe for your org's permissions model.

---

## License

No license file is included yet. Add one before distributing.
