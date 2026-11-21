# Demo Walkthrough

The demo is designed to prove the three scored behaviors in one run:

1. **Decision memory** — earlier decisions are retrieved and reused.
2. **Conflict detection** — contradictions and refinements are surfaced with evidence.
3. **Confirmation gate** — nothing is committed or posted before human approval.

It uses four synthetic meetings in `fixtures/meetings/` and runs entirely offline.

---

## Run it

```bash
uv sync --extra llm --extra dev
uv run mte demo
```

The demo resets `data/demo/` each run so output is reproducible.

---

## The four meetings

### 1. Platform Planning — 2026-11-02

Decisions:

- database / primary database = **Postgres**
- deployment / cloud provider = **AWS in us-east-1**
- beta launch / launch date = **December 4**

Action items:

- Priya: draft the schema (due Nov 6)
- Marcus: benchmark Postgres (due Nov 9)
- unowned: investigate authentication options (no deadline)

Hedged discussion (“Maybe Postgres?”, “I think Postgres is a strong option”) is **not** extracted.

### 2. Platform Follow-up — 2026-11-09

- Postgres migration is delayed by two weeks (a timeline decision, not a database reversal).
- Redis is chosen for caching.
- The beta launch repeats December 4 → detected as a **duplicate**; not written twice.
- “I'll draft the schema” repeats an existing task → detected as a **duplicate task** and skipped.

### 3. Architecture Review — 2026-11-16

- “We will not use Postgres… We'll go with MySQL instead.” → **contradiction** against the active
  Postgres decision. Proposed action: supersede.
- “The beta launch moves to December 18.” → **refinement** of the launch date. Proposed action:
  supersede.
- AWS decision repeats → **duplicate**; not written again.
- “Maybe we should revisit caching too? Let's not decide today.” → ignored.

### 4. Security and Launch Sync — 2026-11-20

- Auth0 is chosen for authentication.
- Beta launch December 18 repeats → **duplicate**.
- “We're not using Redis for caching anymore. We'll use the database-backed cache instead.” →
  **contradiction** against the active Redis decision. Proposed action: supersede.

---

## Expected final state

```text
SUMMARY: 4 meetings | 6 active decisions | 3 superseded | 9 tasks | 9 posted
```

Active decisions after the run:

| Subject | Attribute | Value |
| --- | --- | --- |
| database | primary database | MySQL |
| deployment | cloud provider | AWS in us-east-1 |
| beta launch | launch date | December 18 |
| caching | cache provider | the database-backed cache |
| authentication | auth provider | Auth0 |
| Postgres migration | timeline | delayed by two weeks |

Superseded decisions:

- Postgres (replaced by MySQL)
- December 4 (replaced by December 18)
- Redis (replaced by the database-backed cache)

The demo also:

- **edits** the unowned authentication investigation during the first review, assigning it to
  Elena — showing that the reviewer, not the extractor, has the final say;
- posts nine tasks to the mock tracker (`mock://tracker/issues/1` … `/9`);
- sends reminders for tasks whose deadlines have passed by the simulated “two days from now” clock;
- generates weekly digests for Priya, Marcus, and Elena;
- writes notifications to `data/demo/notifications.log`.

---

## Key output excerpts

### Conflict review

```text
Review packet: Architecture Review (2026-11-16)
Decisions:
  - database / primary database = MySQL  [proposed: supersede]
      CONTRADICTS: 'Postgres' -> 'MySQL'
      reason: The same database/primary database attribute changed from 'Postgres' to 'MySQL'.
  - deployment / cloud provider = AWS in us-east-1  [proposed: duplicate]
      DUPLICATES: 'AWS in us-east-1' -> 'AWS in us-east-1'
  - beta launch / launch date = December 18  [proposed: supersede]
      REFINES: 'December 4' -> 'December 18'
      reason: The subject and attribute are the same, but the date/timeline changed.
```

### Task dedupe

```text
- draft the schema | owner=Priya | due=2026-11-06 | status=skipped
  ! Task 'draft the schema' is a duplicate of existing task ...
```

### Weekly digest

```text
To Marcus:
  Hi Marcus, here is your execution digest for 2026-11-16 to 2026-11-22.

  Due this week:
  - finish the benchmark (due 2026-11-16, posted)

  Coming up:
  - remove the Redis dependency (due 2026-11-27, posted)
```

---

## Run the same flow through the API

```bash
uv run mte serve
```

Then follow the end-to-end example in [`API.md`](API.md). The API stops at
`awaiting_approval`; call `POST /api/reviews/{thread_id}` to commit. Nothing is stored before that
call.

To verify the gate manually:

```bash
# Ingest meeting 1
curl -s -X POST http://127.0.0.1:8000/api/meetings \
  -H 'content-type: application/json' \
  --data @fixtures/meetings/01_platform_planning.json > /tmp/ingest.json

# Before approval: no decisions or tasks
curl -s http://127.0.0.1:8000/api/decisions
curl -s http://127.0.0.1:8000/api/tasks

# Approve using the packet hash, then check again
# (see docs/API.md for the approval script)
```

---

## Run the evaluation

```bash
uv run mte eval
```

Expected:

```json
{
  "passed": true,
  "extraction_precision": 1.0,
  "extraction_recall": 1.0,
  "conflict_precision": 1.0,
  "conflict_recall": 1.0,
  "expected_items": 22,
  "matched_items": 22,
  "expected_conflicts": 6,
  "matched_conflicts": 6
}
```

The eval runs the full graph, including approval and posting, so a failure can come from extraction,
normalization, retrieval, judging, the gate, or the repository.

---

## Verification checklist

- [ ] `uv run pytest` passes.
- [ ] `uv run ruff check src tests` passes.
- [ ] `uv run mte eval` reports `passed: true` with 100% precision/recall.
- [ ] `uv run mte demo` ends with 4 meetings, 6 active decisions, 3 superseded, 9 posted tasks.
- [ ] `GET /api/decisions?status=superseded` returns Postgres, December 4, and Redis.
- [ ] `GET /api/tasks` shows no duplicate `draft the schema` task.
- [ ] `GET /api/conflicts` shows contradictions and refinements with reasoning.
- [ ] Rejecting a review leaves decisions and tasks empty.
- [ ] Re-submitting an old `packet_hash` returns `409`.
