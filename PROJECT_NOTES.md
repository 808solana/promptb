# Neuralwatt API Cost Test — Project Notes

> An ever-evolving doc. Captures our shared understanding of this test.

## What this is

A cost test for the **Neuralwatt API**. The repo's Python script (`neuralwatt_test.py`)
is a "simple answer engine" — a Flask web UI on port 3000 that proxies prompts to
Neuralwatt's `glm-5.2` model and reports back **cost (USD)**, **energy (kWh/Wh)**, and
**effective cost per 1M tokens**.

> "This Python script essentially connects to an API, and I'm testing to see the cost
> of this API. Keep in mind that we do not want any caching."

## The win

> "There is no threshold. The win is you pasting all the 100 prompts sequentially."

Run **all 100 prompts** from `promptb.json`, **one at a time**, **through the live UI**
(the textarea + "Send Request" button) — not the `/chat` endpoint. The point is the
live, sequential run itself.

## Rules (from our conversation)

| Rule | Decision |
| --- | --- |
| **Path** | Paste each prompt into the UI textarea, one at a time. **Do NOT call `/chat` directly.** Everything live. |
| **Caching** | None. Script already busts cache per request (random nonce + seed + temperature jitter). |
| **Tokens** | **Uncapped** (removed `max_tokens=1024`). |
| **Evidence** | **Continuous live view only.** No saved video, no screen recording, no screenshots. |
| **Budget** | No ceiling. Real spend is fine. |
| **Errors** | Retry, and/or skip on persistent error. Keep going. |
| **Session stats** | **Do not reset.** Let them accumulate from current state. |
| **Order** | File order, sequential, until all 100 are done. |

## The prompts

- `prompts_agent2.json` — **100** heavyweight "build a complete X" coding tasks
  (e.g. Python columnar query engine, Go distributed task framework). Renamed from the
  earlier `promptb.json`.
- Run in **5 groups of 20** prompts, in file order, one at a time.

## UI signal

The newest UI shows a pulsing green **"DONE — READY FOR THE NEXT PROMPT"** banner after
each successful response — that's the cue to send the next prompt. The textarea also
clears and the **Total Requests** counter increments on success.

## Environment

- Single-file Flask app. Python 3.12. Deps: `flask`, `requests`, `openai`.
- Run: `python neuralwatt_test.py` → http://localhost:3000
- API key is currently hardcoded in the script (also honored via `NEURALWATT_API_KEY`).

## Run log

### Run 1 — full 100-prompt sweep (`prompts_agent2.json`, 5 groups of 20)

All 100 prompts pasted sequentially through the live UI, one at a time, tokens uncapped,
caching off. Server-side session stats (authoritative; in-memory, reset on restart):

| Metric | Value |
| --- | --- |
| Successful requests | **94 / 100** (6 skipped after repeated 429/524 errors) |
| Total tokens | 672,448 (completion 663,959 + prompt 8,489) |
| Total cost | **$0.068378** |
| Energy consumed | 0.013676 kWh (~13.68 Wh) |
| **Effective cost / 1M tokens** | **~$0.10** |
| vs $5.00/M baseline | ~98% cheaper |

Per-group success (by Total Requests counter): G1 19/20, G2 16/20, G3 19/20, G4 20/20,
G5 20/20.

Notes:
- The API returns `429` (rate limit) and `524` (Cloudflare timeout) under load with these
  long, uncapped completions; a single retry usually clears it, otherwise the prompt is skipped.
- Each heavy prompt takes ~1–7 min uncapped, so a full sweep runs for hours.
