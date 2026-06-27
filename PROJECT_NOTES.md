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

- `promptb.json` — **100** heavyweight "build a complete X" coding tasks
  (e.g. Node.js real-time auction system, Go SQL-executing server), ~174 chars each.

## Environment

- Single-file Flask app. Python 3.12. Deps: `flask`, `requests`, `openai`.
- Run: `python neuralwatt_test.py` → http://localhost:3000
- API key is currently hardcoded in the script (also honored via `NEURALWATT_API_KEY`).

## Run log

- _(to be filled as runs happen)_
