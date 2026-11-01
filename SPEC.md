# SPEC: Agent Traffic Dashboard

> Status: **DRAFT v0.2**. All open decisions in §11 are settled.

## 1. Purpose

`proxy.py` sits between an agent harness (Claude Code) and the model API and
prints every exchange to the terminal. That output is hard to read: one request
can run to thousands of lines of JSON, and requests come in every few seconds.

The dashboard is a local web page that shows the same traffic **live**, laid out
so you can tell at a glance:

- what the harness sent (system prompt, tools, conversation, new message),
- what the model replied (text, thinking, tool calls),
- which tool result goes with which tool call,
- what changed since the last request,
- where something went wrong.

## 2. Users and use

- **User:** a developer or student watching their own agent on their own machine.
- **Typical session:** start the proxy, open the dashboard, run `claude` through
  the proxy, and watch the requests come in. Sessions run for minutes to hours
  and can include hundreds of requests.
- **Not a goal:** production monitoring, multiple users, remote access, editing
  or replaying traffic (see §10).

## 3. How it fits with the proxy

Today the proxy works like this (`proxy.py`):

1. It reads the full request body, prints it, and forwards it with headers intact.
2. It streams the reply back chunk by chunk (`reply.read1`) and prints each chunk.
3. It masks `authorization` / `x-api-key` when printing.
4. It listens on `127.0.0.1:8041` by default and forwards to
   `https://api.anthropic.com` (`--upstream` can change that).

The dashboard needs the proxy to **publish** each exchange as it happens, not
just print it. Rules:

- **R3.1** The proxy must still forward traffic **byte for byte** and with no
  extra delay. The dashboard is only an observer. If the dashboard is slow,
  crashed, or closed, the agent must not notice.
- **R3.2** Each exchange gets an ID and emits these events:
  - `request` — method, path, masked headers, parsed body, timestamp.
  - `response_start` — status code, response headers.
  - `response_chunk` — raw streamed chunks (or events parsed from them).
  - `response_end` — timing, total size, and the final reply rebuilt from the stream.
  - `error` — failure to reach upstream, broken stream, and so on.
- **R3.3** Secrets are never sent to the page: auth headers stay masked the same
  way the terminal output masks them.
- **R3.4** Terminal printing stays as it is (or becomes optional with a flag).
- **R3.5 Decided: built into `proxy.py`.** The same process that relays traffic
  also serves the dashboard. One command, standard library only.
  - Dashboard page: `GET /__dashboard` on the proxy's port
    (e.g. `http://localhost:8041/__dashboard`).
  - Live events: `GET /__dashboard/events`, a Server-Sent Events stream.
  - Paths under `/__dashboard` are handled locally and **never forwarded**
    upstream; every other path is relayed exactly as today.
  - Events are published to an in-process queue per connected browser. A slow
    or disconnected browser drops events for itself only; it never blocks the
    relay thread.
  - On startup the proxy also prints the dashboard URL.

## 4. Data model (what the dashboard understands)

The dashboard parses Anthropic Messages API traffic (`POST /v1/messages`):

| Request field | Shown as |
|---|---|
| `model`, `max_tokens`, `temperature`, `thinking`, `stream`, `metadata`, other top-level keys | Request header strip / "Parameters" |
| `system` (string or list of blocks, maybe with `cache_control`) | **System** section |
| `tools` (name, description, `input_schema`) | **Tools** section |
| `messages[0 .. n-2]` | **Conversation so far** section |
| `messages[n-1]` (usually the latest `user` turn) | **New message** section |

Reply (streamed as server-sent events): `message_start`, `content_block_start`,
`content_block_delta` (`text_delta`, `input_json_delta`, `thinking_delta`),
`content_block_stop`, `message_delta` (`stop_reason`, `usage`), `message_stop`,
`error`. The dashboard rebuilds these into a full message: text blocks, thinking
blocks, and `tool_use` blocks with complete JSON input, plus `stop_reason` and
token `usage`.

Content block types to render: `text`, `thinking` / `redacted_thinking`,
`tool_use`, `tool_result` (whose content can be a string or a list of blocks,
and can have `is_error`), `image`, `document`. Any other block type falls back
to readable JSON.

Other traffic (e.g. `count_tokens`, `HEAD`/`GET` probes, non-JSON bodies) is
listed but shown in a simpler, raw view. **Decided:** these appear in the
timeline as dim, one-line rows (method, path, status, duration). A "Hide
non-message calls" checkbox removes them. They are still marked as errors when
they fail (§8), even when dimmed.

## 5. Layout

```
┌───────────────────────────────────────────────────────────────────────────┐
│ ● Live  |  Filter: [tool ▾] [status ▾] [search ...]  |  ⏸ Pause  | Clear │
├──────────────────────────────┬────────────────────────────────────────────┤
│ TIMELINE (newest first)      │ DETAIL of selected exchange                │
│                              │                                            │
│ #42 12:03:11  ⟳ streaming…   │  Header: #41 · model · 200 · 3.2s ·        │
│ #41 12:03:05  200  3.2s      │          in 18.2k / out 412 tok · cache    │
│     → Bash, Read             │  ─────────────────────────────────────     │
│ #40 12:02:58  ⚠ tool error   │  ▸ Changes since #40  (+2 msgs)            │
│ #39 12:02:51  529 overloaded │  ▸ System (4.1k chars)                     │
│ ...                          │  ▸ Tools (18)                              │
│                              │  ▸ Conversation so far (37 msgs)           │
│                              │  ▾ New message                             │
│                              │      tool_result ← Bash  ✓                 │
│                              │  ▾ Reply                                   │
│                              │      text "I'll check the file…"           │
│                              │      tool_use  Read {path: …}              │
└──────────────────────────────┴────────────────────────────────────────────┘
```

**Decided: two panes**, as above. The timeline on the left is a compact list;
selecting a row shows that exchange's full detail on the right.

- **R5.0a** The newest exchange is auto-selected only while the user has not
  picked one; once they select a row, the selection stays put (see R5.5).
- **R5.0b** Keyboard: `↑`/`↓` (or `j`/`k`) move through the timeline; `Esc`
  returns to following the newest exchange.
- **R5.0c** The divider between the panes can be dragged. On narrow screens
  (< ~900px) the panes stack: list on top, detail below.

### 5.1 Timeline (one row per exchange)

- **R5.1** Newest at the top. New rows appear without a page reload.
- **R5.2** Each row shows: number, time, model (short), HTTP status, duration,
  token counts in/out, tool calls the reply asked for (as chips), and a status
  marker: streaming / done / HTTP error / stream error / contains a tool error.
- **R5.3** Errors stand out by color **and** icon (not color alone).
- **R5.4** A row still streaming updates in place (spinner, live token count).
- **R5.5** If the user has scrolled down or selected an older row, new arrivals
  **do not** move what they are looking at. A "N new ↑" badge appears instead.
- **R5.6** Pause button: stop updating the view (keep collecting in the
  background); resume shows everything missed.

### 5.2 Detail view: sections of a request

Each section has a clear title, a one-line summary when collapsed, and its own
visual style so the sections never blur together.

- **System** — the full system prompt, never shortened when expanded. Multiple
  blocks shown separately, with `cache_control` markers. Summary: size in
  characters/estimated tokens.
- **Tools** — list of tool names with a short description. Expand a tool to see
  its full description and `input_schema` as readable JSON. Summary: count.
- **Conversation so far** — earlier messages, grouped by turn, each labeled
  `user` / `assistant`. Collapsed by default (it repeats the previous request).
- **New message** — the last message(s) added since the previous request in the
  same conversation; expanded by default.
- **Parameters** — model, max_tokens, thinking, temperature, metadata, etc.
- **Reply** — text, thinking (collapsed by default), tool calls, `stop_reason`,
  usage (input, output, cache read, cache write).
- **Raw** — the exact request and reply as received, for debugging.

### 5.3 Tool calls beside their results

The model asks for a tool in reply *N* (`tool_use`, with an `id`); the harness
sends the result in request *N+1* (`tool_result` with a matching
`tool_use_id`).

- **R5.7** Wherever a `tool_use` appears, show its result next to it (side by
  side, or directly underneath on narrow screens), matched by ID.
- **R5.8** If the result has not arrived yet, show "waiting for result…"; fill
  it in live when the next request arrives.
- **R5.9** Link both ways: click the call to jump to the request carrying the
  result, and the other way round.
- **R5.10** Results with `is_error: true` are marked as errors.
- **R5.11** Parallel tool calls (several `tool_use` blocks in one reply) are each
  paired on their own.

### 5.4 What changed since the previous request

- **R5.12** For each request, compare with the previous request **in the same
  conversation** and show: messages added, system prompt changed (with a text
  diff), tools added/removed/changed, parameters changed (e.g. model switch).
- **R5.13** Point out when the history was rewritten rather than appended (e.g.
  context compaction, edited earlier messages), since that is unusual and worth
  noticing.
- **R5.14 Decided: conversations are matched by history.** A request continues
  conversation *C* if its `messages` list begins with *C*'s last request's
  `messages` plus that request's reply (compared by content, ignoring
  `cache_control` markers). If several conversations match, pick the longest
  match; if none match, start a new conversation.
  - This needs no harness-specific headers, so the main agent, each subagent,
    and one-off side calls (e.g. title generation) end up in separate threads.
  - A request that shares a long beginning with *C* but then differs (e.g.
    context compaction, edited history) starts a new conversation marked
    "branched from *C* at message *k*", and R5.13 applies.
  - Each conversation gets a short label (e.g. `main`, `sub-1`, `side-2`) and a
    color shown on its timeline rows. The thread with the most requests is
    labeled `main`. Requests with a single message and no follow-up are labeled
    as side calls.
  - Tool-call pairing (§5.3) and "what changed" (§5.4) work within one
    conversation.

## 6. Formatting rules

- **R6.1 Collapsible long text.** Any text longer than ~15 lines (or ~1,500
  characters) starts collapsed, showing the first lines and a "show all (N
  lines)" control. Expand / collapse all per section.
- **R6.2 Readable JSON.** Pretty-printed, syntax colored, collapsible by node,
  with a copy button. Tool inputs that are JSON strings are parsed and shown as
  JSON.
- **R6.3 Text that is actually text** (system prompt, model text, tool output)
  is shown as text with line breaks kept, not as an escaped JSON string.
  **Decided:** plain monospace by default, so you see exactly what was sent.
  Each text block (system prompt, user/assistant text, thinking) has a
  "Markdown" toggle that renders it; a global setting flips the default.
  Rendered markdown is sanitized (no scripts, no remote images). Tool output
  always stays plain.
- **R6.4 Distinct sections.** Each part of a request has its own label and
  accent color; user, assistant, tool call, tool result, and thinking blocks
  each have a recognizable style.
- **R6.5 Sizes everywhere.** Collapsed items show their size (lines, characters,
  approximate tokens) so you can spot what is heavy.
- **R6.6 Copy.** Copy buttons on every block and on the whole raw request/reply.
- **R6.7** Works in light and dark mode; readable on a laptop screen.

## 7. Filtering and search

- **R7.1** Filter by tool name: show only exchanges whose reply calls that tool
  or whose request carries its result.
- **R7.2** Filter by status: errors only, streaming, HTTP status, tool errors.
- **R7.3** Free-text search across message text, tool inputs, and tool results.
- **R7.4** Filter by model and by conversation (R5.14 labels).
- **R7.5** Filters apply to arriving traffic live.

## 8. Errors made easy to spot

Treat all of these as errors with a visible marker in the timeline and detail:

- proxy could not reach upstream (502 from the proxy),
- HTTP status 4xx/5xx (e.g. 429 rate limit, 529 overloaded) with the error body
  shown readably,
- an `error` event inside the stream, or a stream that ended early,
- `tool_result` with `is_error: true`,
- `stop_reason` of `max_tokens` (shown as a warning, not an error),
- the dashboard's own connection to the proxy dropping (banner + auto-reconnect).

A counter in the top bar shows how many errors happened; clicking it filters to
them.

## 9. Non-functional requirements

- **R9.1 Local only.** Bind to `127.0.0.1`. No data leaves the machine.
- **R9.2 Performance.** Stay responsive with 500+ exchanges whose bodies are
  hundreds of KB each (requests carry the whole conversation every time). Only
  render what is visible; parse large bodies lazily.
- **R9.3 Low latency.** A request appears within ~0.5s of reaching the proxy;
  reply text streams in as it arrives.
- **R9.4 Easy to run.** One command (`python3 proxy.py`); nothing to install.
- **R9.7 Decided: tech stack.**
  - Server: Python standard library only, inside `proxy.py` (R3.5).
  - Front end: a single page of plain HTML/CSS/JavaScript, no framework and no
    build step. It may load a few small libraries from a CDN (e.g. syntax
    highlighting, a JSON viewer, markdown rendering, text diff), each pinned to
    an exact version.
  - The page needs internet access for those libraries. If a library fails to
    load, the page still works with a plain fallback (e.g. uncolored JSON).
- **R9.5 Decided: history lives in memory and is replayed on reload.**
  - The proxy keeps this run's exchanges (all events from R3.2) in memory, up
    to a cap (default 1,000 exchanges, settable with a flag such as
    `--history 2000`). When the cap is hit, the oldest are dropped and the page
    says "N older exchanges dropped".
  - When a page connects (or reconnects after a drop), the proxy first replays
    everything it holds, then continues live. SSE event IDs let a reconnecting
    page ask only for what it missed.
  - Restarting the proxy starts empty. Nothing is written to disk.
  - "Clear" in the page clears the view and asks the proxy to drop its stored
    history.
- **R9.6 Decided: Anthropic format only in v1, with a raw fallback.** Traffic
  that is not Anthropic Messages API (e.g. Codex via `--upstream`) still shows
  in the timeline and its Raw view, with pretty-printed JSON and the streamed
  reply as text. Format-specific parsing sits behind one small adapter (turn a
  request/reply into the sections of §5.2), so a Codex adapter can be added
  later without touching the UI.

## 10. Out of scope (for v1)

- Changing, blocking, or replaying requests.
- Multiple users, login, remote access.
- Cost accounting in dollars.
- Long-term storage / analytics across sessions.

## 11. Open decisions

1. ~~**Integration**~~ — **decided:** built into `proxy.py`, page + SSE under
   `/__dashboard` (§3, R3.5).
2. ~~**Tech stack**~~ — **decided:** stdlib Python server; plain JS page with
   small pinned CDN libraries, no build step (R9.7).
3. ~~**Layout**~~ — **decided:** two panes, timeline + detail (§5).
4. ~~**Conversation grouping**~~ — **decided:** match by history prefix (R5.14).
5. ~~**History**~~ — **decided:** in memory, capped, replayed on page
   (re)connect; no disk (R9.5).
6. ~~**Text rendering**~~ — **decided:** plain by default, per-block markdown
   toggle (R6.3).
7. ~~**Non-message traffic**~~ — **decided:** shown as muted rows, with a hide
   toggle (§4).
8. ~~**Other providers**~~ — **decided:** Anthropic only, raw fallback,
   adapter for later (R9.6).
