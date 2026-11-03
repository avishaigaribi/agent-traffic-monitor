# SPEC.md history

Two commits touch `SPEC.md`. Each reason is tagged with its source:
**[msg]** commit message, **[session]** Claude Code session transcript, **[code]** the build commits' diffs.

## 1. `ee714a9` — Initial SPEC.md draft (2026-11-01), v0.2

**What changed:** New file, 306 lines. It defines the dashboard: purpose, proxy events (R3.x), data model, two-pane layout, tool-call/result pairing, "what changed" diffing, formatting, filters, error handling, non-functional rules, and the decided tech stack (stdlib proxy plus a single HTML page).

**Reason:** The commit message gives none ("Initial SPEC.md draft"), and there was no earlier build to learn from.
- **[session]** The 2026-11-01 session asked for a draft spec for a live traffic dashboard, with "Do NOT write any code" and open decisions raised one at a time. Terminal output was unreadable: one request can run to thousands of lines of JSON. §1 of the spec says the same.
- The spec is at v0.2 because the open decisions in §11 were settled in that session before the commit.

## Build 1 — `e14a7a2` (2026-11-03): rebuilt from scratch
**[session]** Prompt: "build the dashboard from scratch exactly according to SPEC.md… strict time limit of 3 minutes". **[code]** It added `proxy.py` (290 lines) and `dashboard.html` (332 lines), 622 insertions in all. Its message says "fix tool error bug", but the diff has no separate fix.

## 2. `b553a3b` — Update spec: add global session statistics (2026-11-03), v0.3

**What changed:**
- Added the §5.0 Session Statistics bar: Requests, Tokens in/out, Model time. Its rules are R5.0.1–R5.0.8: live updates, overlapping time, failed exchanges, not filtered, survives the history cap and reload, formatting, layout, empty state.
- Added R3.6: the proxy keeps the running totals and sends them as `stats` events.
- Added the stats bar to the layout diagram and to the Purpose list.
- Clarified that dollar cost stays out of scope.

**Reason:** The message says only *what* was added, not why. Other sources:
- **[session]** The user asked for a bar showing total requests, total input/output tokens, and cumulative model time across the session. The build before it, `e14a7a2`, had shown only per-exchange numbers.
- **[code]** `e14a7a2` and `f444224` have no session-level counters. The totals appear only in the next commit, `7c73c3a`.
- **[notes]** Your note says it was "difficult to track overall usage", which matches the above. See the findings.

## The switch from rebuilding to editing

**Yes, it happened after `e14a7a2`.**
- **Rebuilt:** `e14a7a2` was generated from scratch from the spec ([session] "from scratch").
- **Edited:**
  - `f444224` changes 4 lines in `dashboard.html`. [session] The prompt was "fix the error detection logic in dashboard.html and proxy.py".
  - `7c73c3a` is a +153 line update. [session] The prompt was "update dashboard.html and proxy.py exactly according to the new Session Statistics requirements".
- Every change after the first build was an edit. The spec was never rebuilt from scratch again.

## Findings in the traffic

1. **Normal text replies wrongly showed a red "tool error" badge.** [session] This was reported on 2026-11-03.
   - **[code]** `f444224` shows the cause: `toolErrIn` scanned every `tool_result` in the whole replayed history, not just the new ones. It also treated any truthy `is_error` as an error.
   - **Fix:** only `tool_result`s in the last message count, and only when `is_error === true`.
2. **A genuine HTTP 429 from the Anthropic API was caught and shown by the dashboard.** This comes **from your notes only**.
   - Neither the transcripts nor the code record a real 429.
   - The only mentions are the spec's §8 rule ("4xx/5xx, e.g. 429 rate limit") and a few pasted copies of it in the session transcripts.
   - So I can't confirm it from the repo. I'm treating it as your observation.
3. **Overall usage was hard to track.** That led to the aggregate Session Statistics bar, which is the `b553a3b` spec change (v0.3) and the `7c73c3a` code.
