# API Reference

Base URL: `http://127.0.0.1:8000`

Interactive schema: `/docs` (Swagger UI) and `/openapi.json`.

All request and response bodies are JSON. Errors use FastAPI's shape:

```json
{ "detail": "meeting already exists: mtg_001_platform_planning" }
```

---

## Lifecycle

```text
POST /api/meetings
        │
        ▼
meeting.status = processing
        │
        ▼
GET  /api/reviews/{thread_id}       → review packet + packet_hash
        │
        ├── POST approved ──▶ decisions active, tasks posted, reminders scheduled
        │                      meeting.status = approved
        └── POST rejected ──▶ meeting.status = rejected, nothing else written
```

Decision status: `active` → `superseded` (when a later decision replaces it).

Task status: `proposed` → `approved` → `posted`, or `skipped` for duplicates.

---

## Meetings

### `POST /api/meetings`

Ingest a transcript. The pipeline extracts, normalizes, retrieves memory, judges conflicts, and
stops at the approval interrupt.

**Request**

```json
{
  "meeting_id": "mtg_001_platform_planning",
  "title": "Platform Planning",
  "meeting_date": "2026-11-02",
  "attendees": [
    {
      "id": "usr_priya",
      "name": "Priya",
      "aliases": ["Priya Sharma"],
      "email": "priya@example.com"
    },
    {
      "id": "usr_marcus",
      "name": "Marcus",
      "aliases": ["Marcus Lee"],
      "email": "marcus@example.com"
    }
  ],
  "transcript": "[00:00] Priya: Let's start. We'll go with Postgres as the primary database.\n[00:18] Marcus: I'll benchmark it by November 9."
}
```

`meeting_id` is optional; a stable id is derived from title and date. `attendees` must be non-empty
and attendee ids must be unique. Transcript must be non-empty.

**Response `200`**

```json
{
  "thread_id": "thr_7f3a2c1b9d4e",
  "status": "awaiting_approval",
  "review": {
    "thread_id": "thr_7f3a2c1b9d4e",
    "meeting": {
      "id": "mtg_001_platform_planning",
      "title": "Platform Planning",
      "meeting_date": "2026-11-02",
      "attendees": [],
      "transcript": "...",
      "status": "processing",
      "created_at": "2026-11-21T15:00:00Z",
      "decision_count": 0,
      "task_count": 0
    },
    "decisions": [
      {
        "item": {
          "id": "a1b2c3d4e5f6",
          "type": "decision",
          "subject": "database",
          "attribute": "primary database",
          "value": "Postgres",
          "owner": null,
          "owner_text": null,
          "deadline": null,
          "deadline_text": null,
          "confidence": 0.9,
          "source_quote": "We'll go with Postgres as the primary database.",
          "speaker": "usr_priya",
          "flags": []
        },
        "conflicts": [],
        "proposed_conflict_action": "none",
        "proposed_duplicate_of": null
      }
    ],
    "tasks": [
      {
        "id": "task123",
        "meeting_id": "mtg_001_platform_planning",
        "item_id": "item123",
        "title": "benchmark it",
        "body": "Meeting: Platform Planning (2026-11-02)\nOwner: usr_marcus\n...",
        "owner_id": "usr_marcus",
        "owner_name": "Marcus",
        "deadline": "2026-11-09",
        "status": "proposed",
        "dedupe_key": "...",
        "idempotency_key": "mtg_001_platform_planning:item123",
        "tracker_ref": null,
        "tracker_provider": null,
        "duplicate_of": null,
        "created_at": "2026-11-21T15:00:00Z",
        "posted_at": null
      }
    ],
    "warnings": [],
    "packet_hash": "9f2c...",
    "generated_at": "2026-11-21T15:00:00Z"
  }
}
```

**Errors**

| Code | Reason |
| --- | --- |
| `409` | A meeting with that id already exists |
| `422` | Invalid body (empty transcript, duplicate attendee ids, bad date) |
| `500` | Pipeline failure |

### `GET /api/meetings?limit=100`

Returns `MeetingView[]`, newest meeting date first.

### `GET /api/meetings/{meeting_id}`

Returns one `MeetingView` or `404`.

---

## Reviews

### `GET /api/reviews?status=pending&limit=100`

Returns the review queue as `ReviewSummary[]`, newest first. Use `status=pending` for the review
inbox and `status=approved` / `status=rejected` for history.

```json
[
  {
    "thread_id": "thr_7f3a2c1b9d4e",
    "meeting_id": "mtg_001_platform_planning",
    "meeting_title": "Platform Planning",
    "meeting_date": "2026-11-02",
    "status": "pending",
    "packet_hash": "9f2c...",
    "reviewer": null,
    "note": null,
    "created_at": "2026-11-21T15:00:00Z",
    "decided_at": null
  }
]
```

### `GET /api/reviews/{thread_id}`

Returns the current `ReviewPacket`. This is idempotent and safe to poll while the graph is
interrupted. Returns `404` if the thread is unknown.

### `POST /api/reviews/{thread_id}`

Submit the human decision. The body must include the exact `packet_hash` from the packet.

**Approve everything as proposed**

```json
{
  "packet_hash": "9f2c...",
  "status": "approved",
  "decisions": [
    { "item_id": "a1b2c3d4e5f6", "action": "approve", "conflict_action": "none" }
  ],
  "tasks": [
    { "task_id": "task123", "action": "approve" }
  ],
  "reviewer": "priya@example.com",
  "note": "Looks right."
}
```

**Edit an item**

```json
{
  "item_id": "a1b2c3d4e5f6",
  "action": "edit",
  "conflict_action": "supersede",
  "edited": {
    "subject": "database",
    "attribute": "primary database",
    "value": "MySQL",
    "owner_id": "usr_priya",
    "deadline": "2026-11-30",
    "confidence": 0.95
  }
}
```

Task edits use:

```json
{
  "task_id": "task123",
  "action": "edit",
  "edited": {
    "title": "Benchmark MySQL",
    "body": "Updated scope",
    "owner_id": "usr_marcus",
    "deadline": "2026-11-16"
  }
}
```

**Reject**

```json
{
  "packet_hash": "9f2c...",
  "status": "rejected",
  "note": "This was only a discussion; do not record it."
}
```

Rules:

- `action`: `approve`, `edit`, or `reject` (per item).
- `edit` requires an `edited` object.
- Every decision and every non-skipped task must have an entry when approving.
- `conflict_action`: `none`, `supersede`, `keep_both`, or `duplicate`.
  - `supersede` marks old contradicting/refining decisions as superseded.
  - `duplicate` does not insert the new decision; it links to the existing one.
  - `keep_both` inserts the new decision and leaves the old active.
- Edited owners must be attendees of the meeting.

**Response `200`**

```json
{
  "thread_id": "thr_7f3a2c1b9d4e",
  "status": "approved",
  "decisions_saved": 3,
  "tasks_posted": 3,
  "tasks_skipped": 0,
  "superseded": ["old_decision_id"],
  "reminders": [
    {
      "id": 1,
      "task_id": "task123",
      "scheduled_for": "2026-11-07T09:00:00Z",
      "sent_at": null,
      "status": "scheduled",
      "channel": "log",
      "message": "Reminder for Marcus: ..."
    }
  ],
  "posted_tasks": [],
  "message": "Approved 3 decision(s), posted 3 task(s), skipped 0 duplicate(s)."
}
```

For a rejection:

```json
{
  "thread_id": "thr_7f3a2c1b9d4e",
  "status": "rejected",
  "decisions_saved": 0,
  "tasks_posted": 0,
  "tasks_skipped": 0,
  "superseded": [],
  "reminders": [],
  "posted_tasks": [],
  "message": "Review rejected. Nothing was written to memory or posted."
}
```

**Errors**

| Code | Reason |
| --- | --- |
| `404` | Unknown thread |
| `409` | Stale `packet_hash`; reload the packet |
| `422` | Missing verdicts, unknown ids, invalid owner, edit without payload |
| `500` | Commit/post failure |

---

## Decision memory

### `GET /api/decisions?status=active&meeting_id=...&limit=200`

Returns `DecisionView[]`, newest first.

```json
[
  {
    "id": "a1b2c3d4e5f6",
    "type": "decision",
    "subject": "database",
    "attribute": "primary database",
    "value": "MySQL",
    "owner": null,
    "owner_text": null,
    "deadline": null,
    "deadline_text": null,
    "confidence": 0.9,
    "source_quote": "We'll go with MySQL instead.",
    "speaker": "usr_marcus",
    "flags": [],
    "status": "active",
    "meeting_id": "mtg_003_architecture_review",
    "meeting_date": "2026-11-16",
    "created_at": "2026-11-21T15:00:00Z",
    "supersedes": ["old_decision_id"],
    "meeting_title": "Architecture Review"
  }
]
```

`status` accepts `active`, `superseded`, or `revoked`.

### `GET /api/decisions/{decision_id}`

Returns one `DecisionView` or `404`.

### `GET /api/conflicts?meeting_id=...&decision_id=...&relation=...&limit=200`

Returns `ConflictView[]`:

```json
[
  {
    "id": 12,
    "new_item_id": "a1b2c3d4e5f6",
    "old_decision_id": "old123",
    "relation": "contradicts",
    "reasoning": "The same database/primary database attribute changed from 'Postgres' to 'MySQL'.",
    "confidence": 0.84,
    "meeting_id": "mtg_003_architecture_review",
    "created_at": "2026-11-21T15:00:00Z"
  }
]
```

`relation` accepts `contradicts`, `refines`, `duplicates`, or `unrelated`. `unrelated` pairs are not
persisted.

---

## Tasks

### `GET /api/tasks?status=posted&owner_id=usr_priya&limit=200`

Returns `TaskView[]`:

```json
[
  {
    "id": "task123",
    "meeting_id": "mtg_001_platform_planning",
    "item_id": "item123",
    "title": "draft the schema",
    "body": "Meeting: Platform Planning (2026-11-02)\nOwner: usr_priya\n...",
    "owner_id": "usr_priya",
    "owner_name": "Priya",
    "deadline": "2026-11-06",
    "status": "posted",
    "dedupe_key": "...",
    "idempotency_key": "mtg_001_platform_planning:item123",
    "tracker_ref": "mock://tracker/issues/1",
    "tracker_provider": "mock",
    "duplicate_of": null,
    "created_at": "2026-11-21T15:00:00Z",
    "posted_at": "2026-11-21T15:00:01Z"
  }
]
```

`status` accepts `proposed`, `approved`, `skipped`, or `posted`.

### `GET /api/tasks/{task_id}`

Returns one `TaskView` or `404`.

---

## Reminders, digests, notifications

### `GET /api/reminders?limit=200`

Returns scheduled and sent reminders, newest schedule first.

### `POST /api/jobs/reminders/run`

Sends all due reminders immediately. Response:

```json
[
  { "reminder_id": 1, "task_id": "task123", "owner_id": "usr_priya" }
]
```

### `GET /api/digests?limit=100`

Returns stored `DigestView[]`.

### `GET /api/digests/preview?owner_id=usr_priya&week_start=2026-11-16&week_end=2026-11-22`

Builds a digest without saving it. If both dates are omitted, the current UTC week is used. Returns
`404` when the owner has no approved/posted tasks in the week.

### `POST /api/jobs/digest/run?week_start=2026-11-16&week_end=2026-11-22`

Builds, stores, and notifies each owner's weekly digest. Returns the created `DigestView[]`.

### `GET /api/notifications?limit=100`

Returns the persisted notification log (`digest` and `reminder` entries by default).

---

## System

### `GET /api/health`

```json
{
  "status": "ok",
  "app": "Meeting to Execution",
  "environment": "development",
  "llm_enabled": false,
  "tracker_backend": "mock",
  "database": "/home/user/Meeting-to-Execution/data/mte.db"
}
```

### `GET /api/stats`

```json
{
  "meetings": 4,
  "active_decisions": 6,
  "superseded_decisions": 3,
  "tasks": 9,
  "posted_tasks": 9,
  "open_conflicts": 3,
  "pending_reviews": 0
}
```

`open_conflicts` counts all persisted non-`unrelated` conflicts.

---

## End-to-end example

```bash
# 1. Start the API
uv run mte serve

# 2. Ingest a fixture
curl -s -X POST http://127.0.0.1:8000/api/meetings \
  -H 'content-type: application/json' \
  --data @fixtures/meetings/01_platform_planning.json > /tmp/ingest.json

# 3. Inspect the packet
python - <<'PY'
import json
packet = json.load(open('/tmp/ingest.json'))['review']
print(packet['packet_hash'])
for d in packet['decisions']:
    print(d['item']['subject'], d['item']['value'], d['proposed_conflict_action'])
for t in packet['tasks']:
    print(t['title'], t['owner_name'], t['deadline'])
PY

# 4. Approve everything as proposed
python - <<'PY'
import json, urllib.request

body = json.load(open('/tmp/ingest.json'))
packet = body['review']
review = {
    "packet_hash": packet['packet_hash'],
    "status": "approved",
    "decisions": [
        {
            "item_id": entry['item']['id'],
            "action": "approve",
            "conflict_action": entry['proposed_conflict_action'],
        }
        for entry in packet['decisions']
    ],
    "tasks": [
        {"task_id": task['id'], "action": "approve"} for task in packet['tasks']
    ],
    "reviewer": "curl-demo",
}
request = urllib.request.Request(
    f"http://127.0.0.1:8000/api/reviews/{body['thread_id']}",
    data=json.dumps(review).encode(),
    headers={"content-type": "application/json"},
    method="POST",
)
print(json.load(urllib.request.urlopen(request)))
PY
```
