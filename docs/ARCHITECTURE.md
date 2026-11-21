# Architecture

This document explains how the backend works, why the approval gate is trustworthy, and where to
extend it.

---

## 1. System context

```text
┌──────────────┐        ┌──────────────────────────────────────────────┐
│ Web / CLI /  │  HTTP  │ FastAPI                                      │
│ automation   │───────▶│  /api/meetings, /api/reviews, /api/tasks ... │
└──────────────┘        └───────────────────┬──────────────────────────┘
                                            │
                                            ▼
                              ┌─────────────────────────────┐
                              │ Container (composition)     │
                              │  settings, db, embeddings,  │
                              │  vector index, memory,      │
                              │  extractor, judge, tracker, │
                              │  reminders, digests,        │
                              │  scheduler, pipeline        │
                              └──────────────┬──────────────┘
                                             │
               ┌─────────────────────────────┼──────────────────────────────┐
               ▼                             ▼                              ▼
     ┌──────────────────┐         ┌──────────────────┐           ┌──────────────────┐
     │ LangGraph        │         │ SQLite           │           │ Tracker          │
     │ StateGraph +     │         │ meetings,        │           │ mock JSON file   │
     │ SqliteSaver      │         │ decisions, tasks,│           │ or GitHub Issues │
     │ checkpoints      │         │ reviews, etc.    │           └──────────────────┘
     └──────────────────┘         └──────────────────┘
               │
               ▼
     ┌──────────────────┐
     │ sqlite-vec       │
     │ decision vectors │
     └──────────────────┘
```

The API is thin. All business rules live in the graph, memory store, and services, so the CLI,
tests, evals, and future workers use the same path.

---

## 2. Pipeline state machine

`src/meeting_to_execution/graph.py` defines a `StateGraph` with these nodes:

```text
START → ingest → extract → normalize → retrieve → judge → prepare_review → approval
                                                                               │
                                                     ┌─────────────────────────┴─────────────┐
                                                     │ approved                              │ rejected
                                                     ▼                                       ▼
                                                   commit → post → schedule → END           reject → END
```

### State

`GraphState` is a `TypedDict` carrying the meeting, extracted and normalized items, candidate
decisions, conflict verdicts, the review packet and hash, the submitted review, approved records,
posted tasks, reminders, and supersession metadata.

LangGraph checkpoints every transition in `CHECKPOINT_DB` (`SqliteSaver`). The checkpoint serializer
explicitly allows the Pydantic types used in state, so restarting the process does not lose a
pending review.

### Node responsibilities

| Node | Responsibility |
| --- | --- |
| `ingest` | Assign a stable meeting id if absent; persist the meeting as `processing`. |
| `extract` | Produce evidence-backed `ExtractedItem`s via LLM or deterministic rules. |
| `normalize` | Resolve owner aliases and deadlines; validate quotes; add flags; build task drafts. |
| `retrieve` | For each decision, fetch candidate active decisions from other meetings. |
| `judge` | Classify each candidate pair; produce a rationale and confidence. |
| `prepare_review` | Build the review packet, warnings, proposed conflict actions, and packet hash. |
| `approval` | **Interrupt** the graph and wait for a review submission bound to the packet hash. |
| `commit` | Apply the approved plan in one transaction and upsert vectors. |
| `post` | Post approved tasks to the tracker, idempotently. |
| `schedule` | Create reminders for posted tasks with an owner and deadline. |
| `reject` | Mark the review and meeting rejected; write nothing else. |

---

## 3. The approval gate

The gate is the highest-priority safety property.

### Hard interrupt

`approval` calls LangGraph's `interrupt(packet)`. Execution stops and the state is checkpointed.
The API returns the packet with `status: awaiting_approval`. No `commit`, `post`, or `schedule`
node runs.

### Packet hash

`prepare_review` builds the packet with `packet_hash=""`, serializes it with sorted keys and compact
separators, hashes it with SHA-256, and stores the hash in the packet and in the review row.

Approval must echo the exact hash. The graph re-checks it, and the API checks it first to return a
clean `409`. If the packet were regenerated or tampered with, the hash would no longer match.

### Completeness

On approval, the graph verifies:

- every decision id in the packet has a review entry,
- every non-skipped task id has a review entry,
- no review entry references an unknown id,
- edited owners are attendees of the meeting.

Missing entries raise `ValueError`; the API maps validation failures to `422`.

### Rejection

A rejection requires only the packet hash and status. It marks the review rejected and the meeting
rejected. No decisions, tasks, conflicts, tracker issues, or reminders are written.

---

## 4. Extraction and normalization

### Transcript format

The parser accepts lines like:

```text
[00:41] Marcus: We'll go with Postgres as the primary database.
```

Speaker and timestamp are optional; continuation lines are appended to the previous utterance.

### Rule-based extraction

The deterministic extractor is designed for demo/eval reliability, not to replace an LLM:

- decision cues: “we'll”, “we're going with”, “we decided”, “the decision is”, “moved to”,
  “delayed by”, “scheduled for”, etc.
- action cues: “I'll”, “I will”, “someone should”, “can you”, “please”, “action item:”,
  “<Name> will”.
- hedges and questions are rejected unless the utterance also contains an explicit decision cue.
- owners are resolved from speaker pronouns (“I'll” → speaker) or a named attendee.
- deadlines copy the source phrase (“by November 6”) and resolve against the meeting date.

### LLM extraction

When `LLM_API_KEY` is set, `LLMExtractor` sends the transcript, date, and attendee list with a strict
system prompt that requires:

- commitments only, not possibilities,
- no invented owners or deadlines,
- verbatim source quotes,
- a JSON envelope matching `ExtractionEnvelope`.

Any exception or empty result falls back to rules. This keeps local demos and CI deterministic.

### Normalization

`normalize_items`:

- maps owner names/aliases/ids to attendee ids,
- resolves deadlines with `dateparser` anchored to the meeting date (never “today”),
- chooses the nearest year for explicit dates (so “January 9” in November means next January),
- verifies each quote appears in the transcript; otherwise adds `source_quote_not_found` and caps
  confidence at 0.49,
- flags unresolved owners, missing owners, unresolved deadlines, past deadlines, and missing
  decision values,
- assigns deterministic ids derived from meeting, type, subject, attribute, value, and quote.

Deterministic ids make re-ingestion and vector upserts idempotent.

---

## 5. Decision memory

### Storage

`DecisionRow` stores the normalized fields plus:

- `meeting_id` and `meeting_date`,
- `status` (`active`, `superseded`, `revoked`),
- `supersedes` (ids replaced by this decision),
- `created_at`.

`TaskRow` stores the task, owner, deadline, status, dedupe key, idempotency key, tracker reference,
and `duplicate_of`.

`ConflictRow` stores the pair, relation, reasoning, confidence, and meeting.

`ReviewRow` stores the packet hash, status, submission JSON, reviewer, note, and decision time.

### Candidate retrieval

`MemoryStore.candidates` combines:

1. vector search over decision embeddings (`sqlite-vec` cosine distance, or JSON cosine),
2. structured matches on exact subject (and exact attribute when present).

It filters to `active` decisions and excludes the current meeting. Results are sorted by
`matched_subject` first, then similarity, and capped at the requested limit.

The vector backend is selected by `VECTOR_BACKEND`:

- `local` / `sqlite-vec`: load the extension; fall back to JSON on failure.
- `json`: portable cosine over JSON vectors.
- other values raise.

Embeddings are deterministic hashed lexical vectors by default; with an LLM key, OpenAI-compatible
embeddings are used.

### Supersession

On approval, for each decision whose review says `conflict_action=supersede`:

- every contradicting/refining old decision id is recorded in `supersedes`,
- those old rows are set to `superseded`,
- the new decision is inserted as `active`.

For `duplicate`, the new decision is not inserted; the packet records the duplicate target. For
`keep_both`, the new decision is inserted active and the old one remains active.

Because all of this happens inside `commit_plan`'s transaction, there is no window where both old
and new appear active after a successful commit.

---

## 6. Two-stage conflict detection

### Stage 1: retrieval

Candidates come from vector similarity plus structured subject/attribute matches. Retrieval is
deliberately broad (up to 8 per decision) because the judge is the precision layer.

### Stage 2: judging

`RuleBasedJudge` implements the contract:

| Condition | Relation |
| --- | --- |
| Same subject, same attribute, same value | `duplicates` |
| Same subject, same attribute, date-like values differ | `refines` |
| Same subject, same attribute, one value contains the other | `refines` |
| Same subject, same attribute, otherwise | `contradicts` |
| Same subject, different attribute | `unrelated` |
| Different subject | `unrelated` |

Every verdict includes a human-readable reasoning string and confidence. The `LLMJudge` uses the
same schema with a prompt that defines the four relations and requires a JSON object. It falls back
to rules on any error.

### Human resolution

`prepare_review` proposes an action per decision:

- any `contradicts` or `refines` → `supersede`,
- otherwise any `duplicates` → `duplicate`,
- otherwise `none`.

The reviewer can override with `keep_both`, `duplicate`, `supersede`, or `none`, or reject the
item. Conflicts are persisted only for approved decisions and only when the relation is not
`unrelated`.

### Why not auto-resolve?

A changed value is not always a reversal: “Dec 4” → “Dec 18” is a refinement, while
“Postgres” → “MySQL” is a reversal. Even a correct classifier lacks organizational context
(“was the vendor review final?”). The product decision is to surface evidence and require a human.

---

## 7. Execution layer

### Task creation

Each action item becomes a `TaskDraft` with:

- `dedupe_key = hash("task", normalized subject, owner)`,
- `idempotency_key = "{meeting_id}:{item_id}"`,
- a body containing meeting, owner, deadline, and the source quote.

### Duplicate suppression

Before review, `find_task_duplicate` looks for an exact dedupe key or a title similarity ≥ 0.9 for
the same owner. Duplicates are shown as `skipped` with `duplicate_of` and are not posted.

### Tracker adapters

`MockTracker` writes JSON to `data/mock_tracker.json` (or the database directory) and returns
`mock://tracker/issues/{n}` references. It is keyed by idempotency key, so repeated calls return the
same issue with `created=False`.

`GitHubTracker`:

- searches issues in the configured repo for the idempotency marker,
- returns the existing issue if found,
- otherwise creates an issue with an `meeting-action` label and an HTML marker in the body,
- uses a 20-second HTTP timeout and GitHub API version `2022-11-28`.

Both adapters implement the same `Tracker` protocol.

### Reminders

For each posted task with an owner and deadline, `ReminderService` schedules a notification at
09:00 UTC, `REMINDER_HOURS_BEFORE` before the deadline. Past schedule times are moved to one minute
from now so demos still send them. `run_due` sends pending reminders, persists notifications, and
marks them sent.

### Weekly digests

`DigestService` selects approved/posted tasks for an owner whose deadline or creation date falls in
the week. It renders deterministic sections (due this week, coming up, recently posted) or asks the
LLM for prose when configured. Digests are stored and can be previewed without saving.

`run_weekly` finds owners with matching tasks, builds a digest per owner, sends a notification, and
marks the digest sent.

### Scheduler

`SchedulerService` uses APScheduler:

- hourly `run_due` for reminders,
- cron `run_weekly` for digests (`DIGEST_DAY_OF_WEEK`, hour, minute, UTC).

It starts only when `ENABLE_SCHEDULER=true`. The API also exposes manual job triggers for demos and
operations.

---

## 8. Data consistency and failure behavior

- **SQLite transactions.** `commit_plan` writes decisions, supersessions, tasks, conflicts, and
  review status in one session/transaction. Vector upserts happen after the transaction; a crash
  between them can leave a decision without a vector, which retrieval compensates for with
  structured matching and on-demand cosine.
- **Checkpoint durability.** Pending reviews survive process restarts because the interrupt state is
  in `CHECKPOINT_DB`.
- **Stale approvals.** The packet hash prevents approving a packet that has changed since it was
  displayed.
- **Retry safety.** Meeting ids, decision ids, and task idempotency keys are deterministic. Tracker
  posting checks for an existing marker before creating.
- **LLM outages.** Extraction, judging, and digest prose fall back to deterministic behavior.
- **Bad quotes.** Quotes that are not verbatim are flagged and confidence is reduced rather than
  silently trusted.

---

## 9. Extension points

| Need | Seam |
| --- | --- |
| Postgres | Replace `Database`/SQLAlchemy URL and move vector storage to pgvector |
| Real embeddings | Implement `EmbeddingProvider` |
| Different tracker | Implement `Tracker.upsert_issue` |
| Email/Slack | Replace `NotificationService.send` |
| Auth/tenancy | Add middleware and workspace columns; filter repository queries |
| Better extraction | Swap `RuleBasedExtractor` or tune `EXTRACTION_SYSTEM` |
| Better judging | Swap `RuleBasedJudge` or tune `CONFLICT_SYSTEM` |
| Human review UI | Consume the review packet and post a `ReviewSubmission` |

---

## 10. Requirement mapping

| Requirement | Where |
| --- | --- |
| Transcript ingest | `POST /api/meetings`, `MeetingPipeline._ingest` |
| Strict structured extraction | `schemas.py`, `extraction.py`, `normalization.py` |
| Decision memory + vector index | `memory.py`, `vector_index.py`, `embeddings.py` |
| Two-stage conflict detection | `MemoryStore.candidates` + `judge.py` |
| Mandatory human approval | LangGraph `interrupt`, packet hash, completeness checks |
| Idempotent tracker posting | `tracker.py`, deterministic keys |
| Reminders | `reminders.py`, scheduler |
| Weekly per-person digest | `digest.py`, scheduler |
| Checkpointed interrupt/resume | `graph.py`, `SqliteSaver` |
| Evals and tests | `evals/`, `tests/` |
