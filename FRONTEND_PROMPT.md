# Frontend Build Prompt — Meeting to Execution

Copy everything below the line into your AI coding tool (Claude Code, Cursor, Codex, etc.). It is
written to be self-contained. The backend already exists in this repository; do not rebuild it.

---

## Role

You are a senior frontend engineer and product designer. Build a polished, production-quality web
interface for an existing meeting-to-execution backend. You care about trust, clarity, accessibility,
and fast review workflows. You do not modify the backend unless a genuine contract bug is found.

## Product context

The backend turns meeting transcripts into approved, tracked work:

1. A user submits a transcript and attendee list.
2. The backend extracts decisions and action items, normalizes owners and deadlines, retrieves
   related decisions from memory, and classifies conflicts.
3. The pipeline **stops** and returns a review packet.
4. A human reviews every decision and task, resolves conflicts, edits what is wrong, and approves or
   rejects the packet.
5. Only after approval are decisions committed, old decisions superseded, tracker issues posted,
   reminders scheduled, and weekly digests generated.

The product’s core value is **trustworthy memory + a mandatory confirmation gate**. The UI must make
the evidence and consequences obvious. It must never imply that anything was saved before approval.

## Non-goals and hard rules

- Do **not** build authentication, billing, multi-tenancy, or a backend.
- Do **not** invent API endpoints, fields, or status values. Use the contract in this prompt.
- Do **not** auto-approve, auto-submit, or optimistically mark a review as committed.
- Do **not** hide conflicts behind a collapsed section by default. Conflicts are the point.
- Do **not** display generated summaries as if they were verbatim transcript quotes. Quotes must be
  rendered exactly as returned, in a clearly labeled evidence block.
- Do **not** call job endpoints on page load. They send notifications and are explicit actions.
- Do **not** use fake API data in the final app. A local sample transcript constant is fine for the
  ingest form.

---

## Stack

Use:

- **Vite + React 18+ + TypeScript** (strict mode)
- **React Router** for routing
- **TanStack Query** for server state
- **Tailwind CSS** plus a small component layer (shadcn/ui or hand-built primitives)
- **lucide-react** for icons
- **date-fns** for date formatting
- **Zod** for runtime validation of forms where useful

Run the dev server on `http://localhost:5173` (already allowed by backend CORS). Put the API base URL
in `VITE_API_BASE_URL`, defaulting to `http://127.0.0.1:8000`.

Suggested scripts: `dev`, `build`, `preview`, `lint`, `typecheck`, `test`.

---

## Backend contract

Read `docs/API.md` in the repository for full examples. Base URL: `http://127.0.0.1:8000`.

### Endpoints

| Method | Path | Use |
| --- | --- | --- |
| `GET` | `/api/health` | Shell status, backend mode |
| `GET` | `/api/stats` | Dashboard counters |
| `POST` | `/api/meetings` | Ingest transcript; returns `IngestResponse` |
| `GET` | `/api/meetings?limit=` | Meeting list |
| `GET` | `/api/meetings/{meeting_id}` | Meeting detail |
| `GET` | `/api/reviews?status=pending&meeting_id=&limit=` | Review queue |
| `GET` | `/api/reviews/{thread_id}` | Review packet |
| `POST` | `/api/reviews/{thread_id}` | Submit approval/rejection |
| `GET` | `/api/decisions?status=&meeting_id=&limit=` | Decision memory |
| `GET` | `/api/decisions/{decision_id}` | Decision detail |
| `GET` | `/api/conflicts?meeting_id=&decision_id=&relation=&limit=` | Persisted conflicts |
| `GET` | `/api/tasks?status=&owner_id=&limit=` | Tasks |
| `GET` | `/api/tasks/{task_id}` | Task detail |
| `GET` | `/api/reminders?limit=` | Reminders |
| `GET` | `/api/digests?limit=` | Stored digests |
| `GET` | `/api/digests/preview?owner_id=&week_start=&week_end=` | Digest preview |
| `GET` | `/api/notifications?limit=` | Notification log |
| `POST` | `/api/jobs/reminders/run` | Send due reminders now |
| `POST` | `/api/jobs/digest/run?week_start=&week_end=` | Generate digests now |

Errors are `{ "detail": string }`. Important codes:

- `404` unknown resource
- `409` duplicate meeting or stale review packet hash
- `422` validation failure, missing review entries, unknown owner
- `500` pipeline/commit failure

### TypeScript types

Create `src/api/types.ts` with these (adjust formatting only):

```ts
export type ISODate = string; // YYYY-MM-DD
export type ISODateTime = string;
export type ItemType = "decision" | "action_item";
export type DecisionStatus = "active" | "superseded" | "revoked";
export type TaskStatus = "proposed" | "approved" | "skipped" | "posted";
export type ConflictRelation = "contradicts" | "refines" | "duplicates" | "unrelated";
export type ConflictAction = "supersede" | "keep_both" | "duplicate" | "none";
export type ReviewAction = "approve" | "edit" | "reject";
export type ReviewStatus = "pending" | "approved" | "rejected";

export interface Attendee {
  id: string;
  name: string;
  aliases: string[];
  email?: string | null;
}

export interface MeetingCreate {
  meeting_id?: string | null;
  title: string;
  meeting_date: ISODate;
  attendees: Attendee[];
  transcript: string;
}

export interface MeetingView {
  id: string;
  title: string;
  meeting_date: ISODate;
  attendees: Attendee[];
  transcript: string;
  status: string;
  created_at: ISODateTime;
  decision_count: number;
  task_count: number;
}

export interface NormalizedItem {
  id: string;
  type: ItemType;
  subject: string;
  attribute?: string | null;
  value?: string | null;
  owner?: string | null;
  owner_text?: string | null;
  deadline?: ISODate | null;
  deadline_text?: string | null;
  confidence: number;
  source_quote: string;
  speaker?: string | null;
  flags: string[];
}

export interface DecisionView extends NormalizedItem {
  status: DecisionStatus;
  meeting_id: string;
  meeting_date: ISODate;
  created_at: ISODateTime;
  supersedes: string[];
  meeting_title?: string | null;
}

export interface TaskView {
  id: string;
  meeting_id: string;
  item_id: string;
  title: string;
  body: string;
  owner_id?: string | null;
  owner_name?: string | null;
  deadline?: ISODate | null;
  status: TaskStatus;
  dedupe_key: string;
  idempotency_key: string;
  tracker_ref?: string | null;
  tracker_provider?: string | null;
  duplicate_of?: string | null;
  created_at: ISODateTime;
  posted_at?: ISODateTime | null;
}

export interface ConflictView {
  id?: number | null;
  new_item_id: string;
  old_decision_id: string;
  relation: ConflictRelation;
  reasoning: string;
  confidence: number;
  meeting_id?: string | null;
  created_at?: ISODateTime | null;
}

export interface ReviewConflict {
  verdict: ConflictView;
  old_decision: DecisionView;
}

export interface ReviewDecision {
  item: NormalizedItem;
  conflicts: ReviewConflict[];
  proposed_conflict_action: ConflictAction;
  proposed_duplicate_of?: string | null;
}

export interface ReviewPacket {
  thread_id: string;
  meeting: MeetingView;
  decisions: ReviewDecision[];
  tasks: TaskView[];
  warnings: string[];
  packet_hash: string;
  generated_at: ISODateTime;
}

export interface ReviewSummary {
  thread_id: string;
  meeting_id: string;
  meeting_title?: string | null;
  meeting_date?: ISODate | null;
  status: ReviewStatus;
  packet_hash: string;
  reviewer?: string | null;
  note?: string | null;
  created_at: ISODateTime;
  decided_at?: ISODateTime | null;
}

export interface EditableDecision {
  subject: string;
  attribute?: string | null;
  value?: string | null;
  owner_id?: string | null;
  deadline?: ISODate | null;
  confidence: number;
}

export interface EditableTask {
  title: string;
  body?: string | null;
  owner_id?: string | null;
  deadline?: ISODate | null;
}

export interface DecisionReviewInput {
  item_id: string;
  action: ReviewAction;
  edited?: EditableDecision | null;
  conflict_action: ConflictAction;
}

export interface TaskReviewInput {
  task_id: string;
  action: ReviewAction;
  edited?: EditableTask | null;
}

export interface ReviewSubmission {
  packet_hash: string;
  status: "approved" | "rejected";
  decisions: DecisionReviewInput[];
  tasks: TaskReviewInput[];
  note?: string | null;
  reviewer?: string | null;
}

export interface ReviewResult {
  thread_id: string;
  status: string;
  decisions_saved: number;
  tasks_posted: number;
  tasks_skipped: number;
  superseded: string[];
  reminders: Array<Record<string, unknown>>;
  posted_tasks: TaskView[];
  message: string;
}

export interface IngestResponse {
  thread_id: string;
  status: string;
  review: ReviewPacket;
}

export interface StatsView {
  meetings: number;
  active_decisions: number;
  superseded_decisions: number;
  tasks: number;
  posted_tasks: number;
  open_conflicts: number;
  pending_reviews: number;
}

export interface HealthView {
  status: string;
  app: string;
  environment: string;
  llm_enabled: boolean;
  tracker_backend: string;
  database: string;
}

export interface ReminderView {
  id: number;
  task_id: string;
  scheduled_for: ISODateTime;
  sent_at?: ISODateTime | null;
  status: string;
  channel: string;
  message: string;
}

export interface DigestView {
  id: number;
  owner_id: string;
  owner_name?: string | null;
  week_start: ISODate;
  week_end: ISODate;
  text: string;
  created_at: ISODateTime;
  sent_at?: ISODateTime | null;
}

export interface NotificationView {
  id: number;
  kind: string;
  recipient: string;
  subject: string;
  body: string;
  channel: string;
  status: string;
  created_at: ISODateTime;
  sent_at?: ISODateTime | null;
}
```

### API client

Build a typed `apiFetch<T>(path, options)` wrapper that:

- prefixes `VITE_API_BASE_URL`,
- sets `content-type: application/json` when a body exists,
- parses `{ detail }` on non-2xx and throws an `ApiError` with `status` and `detail`,
- supports query params without `undefined` values,
- has a reasonable timeout via `AbortController`.

Use TanStack Query keys such as:

```ts
["health"]
["stats"]
["meetings", { limit }]
["meeting", id]
["reviews", { status }]
["review", threadId]
["decisions", filters]
["decision", id]
["conflicts", filters]
["tasks", filters]
["task", id]
["reminders"]
["digests"]
["digest-preview", ownerId, weekStart, weekEnd]
["notifications"]
```

Invalidate aggressively after a review is submitted: reviews, review, meetings, meeting, decisions,
conflicts, tasks, stats, reminders. Digest/reminder job mutations invalidate their lists and
notifications.

---

## Information architecture

App shell with a left sidebar (collapsible on mobile), top bar, and main content area.

Sidebar navigation:

1. **Dashboard** `/`
2. **Review inbox** `/reviews` with a pending-count badge
3. **Meetings** `/meetings`
4. **Decisions** `/decisions`
5. **Conflicts** `/conflicts`
6. **Tasks** `/tasks`
7. **Operations** `/ops`

Top bar: current page title, a “New meeting” primary button, backend health indicator (green
“Offline pipeline” when `llm_enabled=false`, blue “LLM enabled” when true), and tracker backend label.

Routes:

```text
/                       Dashboard
/meetings               Meeting list
/meetings/new           Ingest form
/meetings/:meetingId    Meeting detail
/reviews                Review inbox
/reviews/:threadId      Review workspace
/decisions              Decision memory
/decisions/:decisionId  Optional detail route or drawer
/conflicts              Conflict explorer
/tasks                  Task board/table
/ops                    Reminders, digests, notifications
*                       Not found
```

---

## Page specifications

### 1. Dashboard `/`

A calm command center, not a wall of charts.

**Header:** “Good morning” style greeting is optional; prefer “Execution overview” plus the current
date. Primary CTA: “Review pending work” if `pending_reviews > 0`, otherwise “New meeting”.

**Stat cards** (responsive 2×2 / 4×1):

- Pending reviews — emphasized with an amber left border when > 0; clicking goes to `/reviews`.
- Active decisions — subtitle “X superseded”.
- Open conflicts — count of contradictions/refinements; clicking goes to `/conflicts`.
- Posted tasks — subtitle “of N tasks”.

**Main grid:**

- Left (wider): **Needs review** list from `GET /api/reviews?status=pending`. Each row: meeting
  title, meeting date, age (“2h ago”), decision/task counts if available (not in summary, so omit
  rather than invent), and a “Review” button.
- Right: **Recent decisions** (first 5 active, newest first) with subject/attribute/value, meeting
  title, and source date. **Upcoming deadlines** from tasks with a deadline, sorted ascending, only
  `approved` or `posted`, limited to 6. Show owner avatar/initials and relative due date.
- Bottom: **Recent conflicts** (first 4) rendered as compact old→new comparison cards with relation
  badges.

**Empty state:** a friendly explanation of the workflow with two actions: “Create your first
meeting” and “Load the demo data” (the latter should be instructions, not a hidden API call; there is
no seed endpoint).

### 2. Review inbox `/reviews`

Tabs or segmented control: **Pending** (default), **Approved**, **Rejected**, **All**. Query
`GET /api/reviews?status=...`.

Each review is a row/card with:

- meeting title and date,
- status badge,
- thread id in muted monospace (copyable),
- created time and decided time,
- reviewer if present,
- primary action: “Open review” for pending, “View outcome” for decided.

Pending rows should be visually prioritized: amber dot, slightly stronger border, no heavy color
fills. Show a helpful banner at the top explaining that nothing is committed until approval.

**Empty pending state:** “No reviews waiting. New meetings will appear here after extraction.”

### 3. New meeting `/meetings/new`

A two-column form with a sticky summary/preview on the right (desktop); single column on mobile.

**Fields:**

- Title (required, 1–200 chars)
- Meeting date (required, date input; default today)
- Attendees editor (at least one):
  - id (required, slug-like, e.g. `usr_priya`; auto-suggest from name but allow editing)
  - name (required)
  - aliases (comma-separated or tag input; optional)
  - email (optional)
  - remove button; “Add attendee” button
- Transcript (required, textarea, monospace, min-height 320px, character count)
  - helper text: “One utterance per line works best: `[00:41] Marcus: We'll go with Postgres.`”
  - “Load sample transcript” button that fills a realistic sample and matching attendees. Keep the
    sample in `src/samples/platformPlanning.ts`. Make it clear this is sample data.
  - optional “Paste from clipboard” is nice but not required.

**Validation:** client-side required fields, unique attendee ids, at least one attendee. Do not block
on optional fields. Show inline errors and a summary.

**Submit:** `POST /api/meetings`.

- Button label “Extract & review”.
- While pending: disable the form, show a progress panel with stages (“Parsing transcript”,
  “Retrieving decision memory”, “Checking conflicts”, “Preparing review”). The backend is a single
  request, so animate stages as indeterminate progress; do not fake completion percentages.
- On success: navigate to `/reviews/{thread_id}` with a toast “Review packet ready”.
- On `409`: explain that a meeting with this id already exists; offer to open the existing meeting.
- On `422`: map the detail to a form-level error.
- On network/500: show a retry panel; preserve form state.

**Right rail:** “What happens next” with 3 steps (extract → review → commit) and a note that nothing
is saved until approval.

### 4. Review workspace `/reviews/:threadId` — the core screen

This is the most important screen. Optimize for careful review, not speed at the expense of trust.

**Layout (desktop):**

- Header: meeting title, date, status, thread id, and a “Back to inbox” link.
- Warning banner if `warnings.length > 0` (amber, list of warnings).
- Two-column body:
  - Main column: decisions, then tasks.
  - Sticky right rail (320–360px): review summary, counts, conflict count, packet hash (truncated
    with copy button), “Approve all as proposed”, “Reject review”, and a short explanation of what
    approval will do.

**Decision cards.** For each `ReviewDecision`:

- Subject / attribute as a heading, value prominent.
- Confidence as a small percentage with a tooltip (“extraction confidence”).
- Owner and deadline chips (if present); flags rendered as warning chips with human labels:
  `owner_missing` → “No owner”, `owner_unresolved` → “Owner not matched”,
  `deadline_unresolved` → “Deadline not resolved”, `deadline_before_meeting` → “Deadline before
  meeting”, `source_quote_not_found` → “Quote not found in transcript”,
  `decision_value_missing` → “Missing value”.
- **Evidence block:** quote in a bordered block with speaker and a “Source” label. Never truncate
  the quote; allow wrapping.
- **Conflicts:** for each `ReviewConflict`, a comparison panel:
  - relation badge: Contradicts (red), Refines (amber), Duplicates (blue), Unrelated (gray; normally
    absent from packets),
  - old decision value on the left, new value on the right, with an arrow,
  - old meeting title/date, old source quote in a secondary evidence block,
  - reasoning text and confidence,
  - a radio/segmented control for `conflict_action`: **Supersede old**, **Keep both**, **Mark
    duplicate** (only meaningful when a duplicate exists). Default to
    `proposed_conflict_action`. Explain each choice in one sentence.
  - For `duplicates`, show “Existing decision” and link to the old decision.
- **Per-item actions:** Approve / Edit / Reject (segmented control or menu).
  - Approve uses the extracted values.
  - Edit reveals inline fields: subject, attribute, value, owner select (attendees only), deadline
    date, confidence (0–1 slider or number, default unchanged). For decisions, `edited` must include
    all editable fields the backend expects; send the full edited object.
  - Reject excludes this item from commit. It does **not** reject the whole review.
- If a decision is marked `duplicate`, make it clear it will not be written as a new decision.

**Task cards.** For each `TaskView`:

- Title, owner, deadline, status.
- If `status === "skipped"`: render a “Duplicate” badge, explain it will not be posted, show
  `duplicate_of`, and disable editing. It still needs a review entry? The backend requires every
  non-skipped task only; the UI may include it with `action: "approve"` harmlessly, but prefer to
  show it read-only and still send an approve entry for completeness.
- If owner is missing, show an amber “Unassigned” chip and encourage assignment via Edit.
- Edit fields: title, body (textarea, optional), owner select, deadline.
- Reject means the task will not be posted.
- Show tracker ref if already posted (normally not during review).

**Right rail summary:**

- Decisions: N total, X approved, Y edited, Z rejected.
- Tasks: N total, X approved, Y edited, Z rejected, D duplicates skipped.
- Conflicts: count by relation.
- A compact “Will commit” statement: “3 decisions saved · 2 tasks posted · 1 superseded”.
  Recompute live from local state; this is a preview, not a server claim.
- Primary button “Approve & commit” disabled until every decision and every non-skipped task has an
  explicit action. Since default state is “approve as proposed”, this will normally be enabled
  immediately; still show the completeness check.
- Secondary “Reject review” opens a confirmation dialog with an optional note. Rejection sends
  `status: "rejected"` and no item verdicts are required.
- A details disclosure showing the full packet hash.

**Submission behavior:**

- Build `ReviewSubmission` exactly:
  - `packet_hash` from the loaded packet,
  - `status: "approved"` or `"rejected"`,
  - `decisions`: one entry per decision, with `action`, `conflict_action`, and `edited` only when
    action is `edit`,
  - `tasks`: one entry per task, with `edited` only when action is `edit`,
  - optional reviewer (a simple text input in the rail or a settings default) and note.
- Disable the form while submitting. Do not optimistically navigate.
- On `200`: show a success panel with `message`, counts, superseded decisions, and posted tasks.
  Provide “View tasks”, “View decisions”, and “Back to dashboard”. Invalidate queries.
- On `409` (stale packet): show a blocking dialog: “This review changed since you opened it.” Button
  “Reload review” refetches the packet and discards local edits after confirmation.
- On `422`: show the `detail` prominently; keep local edits so the reviewer can fix the issue.
- On `500`: show a retry option and a note that the commit is atomic; advise reloading before
  retrying if unsure.

**Accessibility:** all action groups are keyboard operable, conflict choices use real radio inputs
or accessible buttons with `aria-pressed`, warnings use `role="status"` or `aria-live="polite"` when
updated, and focus moves to the success/error panel after submission.

### 5. Meetings `/meetings` and `/meetings/:id`

**List:** table/cards with title, date, status, decision count, task count, and a link. Filter by
status (`processing`, `approved`, `rejected`) client-side or via refetch. Search by title client-side.

**Detail:** meeting metadata, attendees, full transcript in a collapsible monospace panel, decisions
for the meeting, tasks for the meeting, and a link to the review if status is `processing` (use the
review queue filtered by meeting id; if none found, explain that the review may have expired).

### 6. Decisions `/decisions`

A searchable memory browser.

- Filters: status (active/superseded/revoked/all), meeting, free-text search across subject,
  attribute, value, quote.
- Table columns: status, subject, attribute, value, meeting, date, confidence.
- Row click opens a detail drawer/route with:
  - full structured fields,
  - source quote and speaker,
  - flags,
  - supersedes links (clickable chips),
  - conflicts involving this decision (query `/api/conflicts?decision_id=...`),
  - meeting link.
- Superseded rows are visually muted with a “Superseded” badge; do not hide them by default when
  “All” is selected.
- Show an explanatory empty state: “Approved decisions will appear here after a review.”

### 7. Conflicts `/conflicts`

An evidence-first explorer.

- Filter by relation (contradicts/refines/duplicates/all), meeting, and decision.
- Each conflict card shows:
  - relation badge and confidence,
  - new item id and old decision id (copyable, muted),
  - reasoning,
  - a link to the meeting and to the decisions.
- Because `ConflictView` does not embed subjects/values, fetch the referenced decisions
  (`/api/decisions/{id}`) or render ids with links. Prefer a small batched cache of decision
  lookups; do not invent data. If lookups fail, show ids and an error affordance.
- Header explains the difference: contradiction = same attribute, different value; refinement =
  same attribute, changed date/timeline; duplicate = same value.

### 8. Tasks `/tasks`

Default view: a board with columns **Posted**, **Approved**, **Skipped**, **Proposed** (in that
order), or a table toggle. Cards show title, owner, deadline, tracker ref (external link when
`http(s)`, plain text for `mock://`), meeting, and status.

- Filters: owner, status, overdue toggle, search.
- Overdue = deadline < today and status not `posted`/`skipped`.
- Detail drawer: full body, idempotency key, dedupe key, duplicate link, tracker link.
- Empty state: “No tasks yet. Approve a meeting review to create tracked work.”

### 9. Operations `/ops`

Three sections, each with an explicit action and confirmation:

1. **Reminders** — list from `/api/reminders`, status chips (scheduled/sent), scheduled time,
   message, task link. Button “Send due reminders now” → `POST /api/jobs/reminders/run`; confirm,
   then toast with the number sent and invalidate reminders/notifications.
2. **Weekly digests** — owner id + week start/end inputs (default current week), “Preview” calls
   `/api/digests/preview`; render the text in a readable prose panel with a copy button. “Generate &
   send” calls `/api/jobs/digest/run` with the chosen week; confirm because it sends notifications.
   List stored digests below with owner, week, sent time, and expandable text.
3. **Notifications** — table/log of kind, recipient, subject, status, created time; expandable body.

Add a small note that jobs also run on the backend scheduler; manual triggers are for demos and
operations.

### 10. Not found and error states

- 404 route with a link home.
- Query error component with the API `detail`, a retry button, and a hint to check that the backend
  is running at the configured URL.
- Global error boundary with a reload action.

---

## Design direction

Aim for **calm, editorial, high-trust**. This is a decision record, not a growth dashboard.

- **Surfaces:** warm off-white app background (`#F7F7F5`), white cards, subtle 1px borders
  (`#E5E7EB`), very soft shadows only on overlays.
- **Ink:** near-black `#171717`, secondary `#525252`, muted `#737373`.
- **Accents:** indigo/blue for primary actions (`#3157D5`), green for approved/posted (`#067647`),
  amber for needs-review/refines (`#B45309`), red for contradictions/rejections (`#B42318`).
- **Typography:** Inter or IBM Plex Sans for UI; a serif such as Source Serif 4 for page titles and
  source quotes is a nice editorial touch. Use tabular numbers for stats.
- **Shape:** 10–12px radii, 1px borders, no glassmorphism, no neon gradients, no giant emoji.
- **Spacing:** 8px scale; page padding 24–32px; max content width 1440px.
- **Density:** comfortable. Decision cards need room for evidence and conflict comparisons.
- **Badges:** small dot + label; avoid rainbow color coding.
- **Motion:** 120–180ms ease-out for drawers/toasts; respect `prefers-reduced-motion`.
- **Dark mode:** optional, but if implemented, use CSS variables and verify contrast.

**Conflict comparison component:** two columns with a center arrow; old value in a muted/red-tinted
panel, new value in a green-tinted panel. Relation badge above. Reasoning below in smaller text.
On mobile, stack old above new with a downward arrow.

**Source quote component:** left border in the accent color, speaker name in small caps, quote in
regular text, optional timestamp if you parse one from the quote/transcript. Label it “From the
transcript”.

---

## State and data rules

- Server state lives in TanStack Query. Local review edits live in a reducer keyed by item/task id.
- Initialize local state once per packet hash. If the packet hash changes (refetch after 409), reset
  local state and warn.
- Track `isDirty` per item and globally; offer “Reset changes”.
- Do not use `index` as a key; use ids.
- Dates: display `MMM d, yyyy`; datetimes with local timezone and a tooltip showing ISO. Treat
  `YYYY-MM-DD` as a calendar date, not UTC midnight, to avoid off-by-one display.
- Confidence: show as `Math.round(value * 100)%`.
- Status labels: map snake_case to human labels; keep a single mapping module.
- Owner display: prefer `owner_name`, then attendee lookup, then `owner_id`, then “Unassigned”.
- Numbers: use `Intl.NumberFormat`.

### Submission builder (important)

Write a pure function `buildSubmission(packet, draftState, mode, note, reviewer)` with unit tests.
It must:

- include one decision entry per packet decision, in packet order,
- include one task entry per packet task, in packet order,
- default action `approve` and `conflict_action` to the proposed value,
- send `edited` only for `edit`,
- send `status: "rejected"` with empty arrays for a whole-review rejection,
- never mutate the packet,
- preserve the exact `packet_hash`.

---

## Suggested file structure

```text
src/
  api/
    client.ts
    types.ts
    queries.ts
    mutations.ts
  components/
    layout/AppShell.tsx
    layout/Sidebar.tsx
    layout/TopBar.tsx
    ui/Button.tsx, Badge.tsx, Card.tsx, Dialog.tsx, Tabs.tsx, ...
    domain/StatCard.tsx
    domain/StatusBadge.tsx
    domain/Confidence.tsx
    domain/SourceQuote.tsx
    domain/ConflictComparison.tsx
    domain/DecisionCard.tsx
    domain/TaskCard.tsx
    domain/OwnerSelect.tsx
    domain/EmptyState.tsx
    domain/ErrorState.tsx
  features/
    dashboard/DashboardPage.tsx
    meetings/MeetingListPage.tsx
    meetings/MeetingDetailPage.tsx
    meetings/NewMeetingPage.tsx
    reviews/ReviewInboxPage.tsx
    reviews/ReviewWorkspacePage.tsx
    reviews/reviewDraft.ts
    reviews/buildSubmission.ts
    decisions/DecisionsPage.tsx
    conflicts/ConflictsPage.tsx
    tasks/TasksPage.tsx
    ops/OperationsPage.tsx
  samples/platformPlanning.ts
  lib/format.ts
  lib/labels.ts
  App.tsx
  main.tsx
  index.css
```

---

## Acceptance criteria

The build is done when all of these are true:

- [ ] `npm run typecheck`, `npm run lint`, and `npm run build` pass with no errors.
- [ ] The app runs on `localhost:5173` against `localhost:8000` with no CORS errors.
- [ ] A user can ingest the sample transcript and land on the review workspace.
- [ ] The review workspace shows every decision and task, source quotes, flags, and conflicts.
- [ ] Conflict actions can be changed per decision and are sent correctly.
- [ ] Decisions and tasks can be edited; edits are sent as `action: "edit"` with full `edited`
      objects.
- [ ] Individual items can be rejected without rejecting the whole review.
- [ ] “Approve & commit” requires a verdict for every decision and non-skipped task.
- [ ] Nothing is shown as committed before the POST succeeds.
- [ ] A stale packet hash (`409`) triggers a clear reload flow and discards stale edits only after
      confirmation.
- [ ] A whole-review rejection sends `status: "rejected"` and shows the “nothing was written”
      message.
- [ ] After approval, the success panel reports counts and links to tasks/decisions.
- [ ] Dashboard pending count, review queue, decisions, conflicts, tasks, reminders, digests, and
      notifications all load real backend data.
- [ ] Empty, loading, error, and 404 states exist for every page.
- [ ] Layout is responsive at 375px, 768px, 1280px, and 1440px.
- [ ] Keyboard navigation works for the review flow; focus states are visible; color contrast meets
      WCAG AA; status changes are announced.
- [ ] No fake API responses, no auto-approval, no backend changes.

---

## Demo script for the finished UI

1. Start the backend: `uv run mte serve` (from the repository root).
2. Start the frontend: `npm run dev`.
3. Open `/meetings/new`, click “Load sample transcript”, submit.
4. On the review workspace, point out the source quote, the proposed decisions, and the unassigned
   authentication task.
5. Edit the authentication task to assign it to Elena.
6. Approve and show the success summary.
7. Ingest the second fixture (or a sample follow-up) and show the duplicate beta-launch decision and
   skipped duplicate task.
8. Ingest the architecture-review sample and show the Postgres → MySQL contradiction and Dec 4 →
   Dec 18 refinement, then supersede both.
9. Visit Decisions and show the superseded chain; visit Conflicts and show the reasoning; visit
   Tasks and show posted tickets.
10. Visit Operations, preview a digest, and run the weekly digest job.

If you need more sample transcripts, use the JSON files in `fixtures/meetings/` as the source of
truth. Copy their transcripts into `src/samples/` at build time rather than reading server files at
runtime.
