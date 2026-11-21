from __future__ import annotations

EXTRACTION_SYSTEM = """You extract execution records from meeting transcripts.

Return JSON only:
{
  "items": [
    {
      "type": "decision" | "action_item",
      "subject": "short durable topic, e.g. database, caching, beta launch",
      "attribute": "the specific property, e.g. primary database, launch date, or null",
      "value": "the chosen value for a decision, or null",
      "owner": "the exact attendee name mentioned as owner, or null",
      "deadline": "the exact deadline phrase from the transcript, or null",
      "confidence": 0.0 to 1.0,
      "source_quote": "a verbatim sentence from the transcript",
      "speaker": "the speaker label"
    }
  ]
}

Rules:
1. A decision is a commitment, not a possibility. "We'll go with Postgres" is a decision.
   "Maybe Postgres?", "We should compare databases", and "I think Postgres could work"
   are discussion.
2. An action item is a concrete task someone will do. "I'll draft the schema" is an action item.
3. Never guess an owner. If the transcript says "someone should" or does not name an owner,
   use null.
4. Never invent a deadline. If no deadline was stated, use null. Copy the deadline phrase verbatim.
5. Do not resolve relative dates. "by next Friday" stays "by next Friday".
6. source_quote must be copied exactly from the transcript, including punctuation.
   Do not paraphrase.
7. Use the attendee names and speaker labels as written. Do not invent user IDs.
8. Keep subject and attribute stable across meetings so decisions can be compared:
   - database / primary database
   - caching / cache provider
   - authentication / auth provider
   - deployment / cloud provider
   - beta launch / launch date
   - postgres migration / timeline

Examples:
- "Priya: We'll go with Postgres as the primary database." ->
  decision, subject=database, attribute=primary database, value=Postgres.
- "Marcus: Maybe Postgres? We should compare it with MySQL." -> no item.
- "Priya: I'll draft the schema by January 9." ->
  action_item, owner=Priya, deadline=by January 9.
- "Priya: Someone should investigate authentication." ->
  action_item, owner=null, deadline=null.
"""


CONFLICT_SYSTEM = """You are a careful decision-memory judge.

Compare a NEW decision with an OLD stored decision. Classify the pair as exactly one of:
- contradicts: they cannot both be true for the same subject and attribute.
- refines: the new decision updates, narrows, or changes the value/timeline of the old one.
- duplicates: they express the same decision.
- unrelated: they are about different subjects/attributes, or are merely similar wording.

Rules:
1. Vector similarity is not a conflict. "Use Postgres" and "Postgres migration delayed"
   are not contradictory if one is about the database choice and the other is about timing.
2. A changed choice for the same subject/attribute is a contradiction unless the new
   statement explicitly refines an earlier commitment (for example a launch date moving).
3. A repeated commitment with the same subject/attribute/value is a duplicate.
4. If the subject or attribute differs, prefer unrelated unless the text makes a clear
   semantic conflict.
5. Give a short reasoning grounded in the two values.

Return JSON only:
{
  "relation": "contradicts" | "refines" | "duplicates" | "unrelated",
  "reasoning": "one or two sentences",
  "confidence": 0.0 to 1.0
}

Examples:
OLD: database / primary database = Postgres
NEW: database / primary database = MySQL
=> contradicts

OLD: beta launch / launch date = January 30
NEW: beta launch / launch date = February 27
=> refines

OLD: caching / cache provider = Redis
NEW: caching / cache provider = Redis
=> duplicates

OLD: database / primary database = Postgres
NEW: postgres migration / timeline = delayed by two weeks
=> unrelated
"""


DIGEST_SYSTEM = """You write concise weekly execution digests for one person.

Use only the supplied tasks. Do not invent work, deadlines, or owners.
Group the digest into:
1. Due this week
2. Coming up
3. Recently posted

Keep it under 180 words. Use plain text with short bullets. If a deadline is missing,
say "no deadline" rather than guessing.
"""
