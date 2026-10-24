# SecondBrain — "Today" screen broken (JSON contract drift)

**Reported by Gene 2026-09-28.** Screenshot: under **Today**,

> Could not load: Unexpected JSON token at offset 5087: Expected quotation mark '"',
> but had '1' instead at path: `$.attention.items[0].id`
> JSON input: `.....on":{"open":10,"items":[{"id":14,"signal_id":"rules_pending_...`

Locally reproducible: the app's own error path names `attention.items[0].id`.

## What is actually wrong — MEASURED, not inferred

Live `GET /attention` (server on :8823, real payload, 5166 B) returns:

```json
{"open": [ {"id": 17, "signal_id": "entity_duplicates", ...}, ... ],
 "tripped": [...], "top_severity": "low", "count": 3}
```

Three independent contract drifts, all in one endpoint:

| # | Server ships | Kotlin DTO declares | Verdict |
|---|---|---|---|
| 1 | `open` = **array** of items | `AttentionSummaryDto.open: Int = 0` | **type conflict** |
| 2 | item key is **`open`** | expects key **`items`** | **name drift** |
| 3 | `id` = **int** (17,16,15) | `AttentionItemDto.id: String = ""` | **type conflict** |
| 4 | `action_text` | `action: String? = null` | name drift (non-fatal, nullable) |

The screenshot error is **#3**: `Expected quotation mark '"', but had '1'`.
`v` in the message is kotlinx-serialization's short name for the *string*
serializer; the token `1` is an integer. That is an int-vs-string mismatch and
nothing else — offsets and `items` vs `open` are red herrings for that message.

## WHICH SIDE IS AUTHORITATIVE

**The server, and the client must change.** Evidence:

- `server/app/db.py` (the `attention_items_v2` rebuild) declares
  `id INTEGER PRIMARY KEY AUTOINCREMENT`.
- `POST /attention/{item_id}/acknowledge` is typed `item_id: int`
  (`thinking.py:229`) and `attention.acknowledge()` binds it to `WHERE id=?`.
  So the id is *structurally* an int on the server.
- **The client already disagrees with itself:** `SecondBrainApi.kt:475` declares
  `acknowledgeAttention(@Path("id") id: String)`. The DTO and the path param
  cannot both be right; the server says String is the wrong one.
- `main.py` / route layer sends `dict(r)` straight off a `SELECT *`, so the
  server has no serialization model to "fix" — changing it means writing a
  mapper. The client already has a model; it is the piece that is wrong.

## Consequence today

`Today` **cannot load at all** while any attention item is open. It is not a
cosmetic warning: the screen is dead. `items` never deserializes, `open` is an
array being read as an Int, and any open item kills the parse.

## Fix shape (client-side, one file + call sites)

1. `AttentionItemDto.id` → `Int`, `action` → carry `@SerialName("action_text")`,
   add `signal_id`/`created_at`/`acknowledged_at` (or ignore explicitly).
2. `AttentionSummaryDto.open` → `List<AttentionItemDto>`; the *count* moves to
   the already-present `count` field. Add `tripped`.
3. `acknowledgeAttention(@Path("id") id: Int)`.
4. Sweep call sites for `.open` used as an Int.
5. **Add a contract test** that fails when a DTO and the live payload disagree —
   this class of bug is exactly what the app cannot see for itself.

## Why this was invisible until now

There is no test that compares a DTO against a real server payload, and the
`Today` screen is the only consumer. The route is also *newer* than the client's
model of it (`0eac109` added `AttentionItemDto` with `id: String` and
`id: String` path param in the same commit) — the drift was born with the code.

## NOT mine to claim yet

I have not run the app to confirm the post-fix load, and I have not verified
whether other endpoints share the pattern (`dict(r)` off `SELECT *` with a
hand-written DTO on the client). Until a fix is installed on the phone and
`Today` renders, this stays a diagnosed-but-unverified bug.