"""
Neuralwatt Proxy Server (hardened)
==================================
Sits between Cursor (or any OpenAI-compatible client) and Neuralwatt.
- Accepts requests using YOUR custom model names
- Rewrites model names to Neuralwatt's actual model IDs
- Forwards requests to Neuralwatt with your API key
- Tracks usage and cost per API key
- Runs on port 4000 (so it doesn't conflict with the tester on 3000)

What changed vs the original (the timeout/"chop" fixes):
- NO read-timeout cap on the upstream call -> long generations are never cut off.
  We only keep a short CONNECT timeout so a dead endpoint still fails fast.
- API key is read correctly (env var NEURALWATT_API_KEY, else the baked-in default).
- Streaming passes the upstream bytes through VERBATIM (no line reassembly / no
  mangling of multi-line SSE events or keep-alive comments) and is flushed
  immediately with anti-buffering headers.
- `stream_options.include_usage` is requested so usage is captured on streamed calls.
- `debug=True` removed; dev server runs threaded. Use gunicorn/uvicorn in production
  (see the run notes at the bottom).

Setup:
    pip install flask requests

Local run:
    python proxy.py
In Cursor, set:
    Base URL: http://localhost:4000/v1   (or https://api.yourdomain.com/v1)
    API Key:  any string you want (used only for per-customer usage tracking)
    Model:    any name from MODEL_MAP below
"""

import os
import json
import requests
from flask import Flask, request, jsonify, Response, stream_with_context
from datetime import datetime

# ── CONFIG ────────────────────────────────────────────────────────────────────
# Read from the environment first; fall back to the baked-in key.
NEURALWATT_API_KEY = os.getenv(
    "NEURALWATT_API_KEY",
    "sk-3cc59661cd4a84270dfbbd49783e4c440e97e16384826c3b85b191d5cb5d780c",
)
NEURALWATT_BASE_URL = os.getenv("NEURALWATT_BASE_URL", "https://api.neuralwatt.com/v1")
PORT = int(os.getenv("PORT", "4000"))

# Only a CONNECT timeout (seconds). Read timeout is None => never cut off a long
# generation. (connect, read) tuple per the requests library.
CONNECT_TIMEOUT = float(os.getenv("PROXY_CONNECT_TIMEOUT", "15"))
UPSTREAM_TIMEOUT = (CONNECT_TIMEOUT, None)

# ── MODEL MAP ─────────────────────────────────────────────────────────────────
# Left  = what YOUR customers use (the slug they put in Cursor)
# Right = what gets sent to Neuralwatt's API
MODEL_MAP = {
    "daglm-5.2":                  "glm-5.2",
    "my-glm-5.2-fast":            "glm-5.2-fast",
    "my-kimi-code":               "moonshotai/Kimi-K2.7-Code",
    "my-qwen3":                   "Qwen/Qwen3.6-35B-A3B",
    # Pass through real names unchanged (fallback)
    "glm-5.2":                    "glm-5.2",
    "glm-5.2-fast":               "glm-5.2-fast",
    "moonshotai/Kimi-K2.7-Code":  "moonshotai/Kimi-K2.7-Code",
    "Qwen/Qwen3.6-35B-A3B":       "Qwen/Qwen3.6-35B-A3B",
}

# ── YOUR PRICING (what you charge customers per million tokens) ───────────────
YOUR_INPUT_PRICE_PER_M = 0.13   # $0.13 per million input tokens
YOUR_OUTPUT_PRICE_PER_M = 0.23  # $0.23 per million output tokens

# ── FLASK APP ─────────────────────────────────────────────────────────────────
app = Flask(__name__)

usage_tracker = {}


def track_usage(api_key, model, prompt_tokens, completion_tokens, neuralwatt_cost):
    """Track usage and calculate your revenue vs cost."""
    if api_key not in usage_tracker:
        usage_tracker[api_key] = {
            "total_requests": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "neuralwatt_cost_usd": 0.0,
            "your_revenue_usd": 0.0,
            "your_profit_usd": 0.0,
            "models_used": {},
        }

    entry = usage_tracker[api_key]
    entry["total_requests"] += 1
    entry["total_prompt_tokens"] += prompt_tokens
    entry["total_completion_tokens"] += completion_tokens
    entry["total_tokens"] += prompt_tokens + completion_tokens
    entry["neuralwatt_cost_usd"] += neuralwatt_cost or 0.0

    your_revenue = (
        (prompt_tokens / 1_000_000 * YOUR_INPUT_PRICE_PER_M)
        + (completion_tokens / 1_000_000 * YOUR_OUTPUT_PRICE_PER_M)
    )
    entry["your_revenue_usd"] += your_revenue
    entry["your_profit_usd"] = entry["your_revenue_usd"] - entry["neuralwatt_cost_usd"]

    if model not in entry["models_used"]:
        entry["models_used"][model] = {"requests": 0, "tokens": 0}
    entry["models_used"][model]["requests"] += 1
    entry["models_used"][model]["tokens"] += prompt_tokens + completion_tokens


def _extract_usage_from_sse_text(text, state):
    """Best-effort parse of `usage` from streamed SSE text without altering bytes.

    `state` is a dict with a 'buf' string carrying any partial trailing line.
    Updates state['prompt_tokens'] / state['completion_tokens'] when found.
    """
    state["buf"] += text
    while "\n" in state["buf"]:
        line, state["buf"] = state["buf"].split("\n", 1)
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except Exception:
            continue
        usage = data.get("usage")
        if isinstance(usage, dict):
            state["prompt_tokens"] = usage.get("prompt_tokens", state["prompt_tokens"])
            state["completion_tokens"] = usage.get(
                "completion_tokens", state["completion_tokens"]
            )


# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.route("/v1/models", methods=["GET"])
def list_models():
    """Return your branded model list to Cursor."""
    models = [
        {"id": slug, "object": "model", "created": 1700000000, "owned_by": "neuralwatt-proxy"}
        for slug in MODEL_MAP.keys()
    ]
    return jsonify({"object": "list", "data": models})


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    customer_api_key = request.headers.get("Authorization", "unknown").replace("Bearer ", "")

    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Invalid JSON body"}), 400

    requested_model = body.get("model", "glm-5.2")
    body["model"] = MODEL_MAP.get(requested_model, requested_model)

    headers = {
        "Authorization": f"Bearer {NEURALWATT_API_KEY}",
        "Content-Type": "application/json",
    }

    is_streaming = bool(body.get("stream", False))

    # Ask upstream to include token usage in the final streamed chunk.
    if is_streaming:
        opts = body.get("stream_options") or {}
        opts.setdefault("include_usage", True)
        body["stream_options"] = opts

    try:
        if is_streaming:
            def generate():
                state = {"buf": "", "prompt_tokens": 0, "completion_tokens": 0}
                with requests.post(
                    f"{NEURALWATT_BASE_URL}/chat/completions",
                    headers=headers,
                    json=body,
                    stream=True,
                    timeout=UPSTREAM_TIMEOUT,
                ) as r:
                    # Pass upstream bytes through verbatim; never reassemble/mangle.
                    for chunk in r.iter_content(chunk_size=None):
                        if not chunk:
                            continue
                        try:
                            _extract_usage_from_sse_text(
                                chunk.decode("utf-8", "replace"), state
                            )
                        except Exception:
                            pass
                        yield chunk
                track_usage(
                    customer_api_key,
                    requested_model,
                    state["prompt_tokens"],
                    state["completion_tokens"],
                    neuralwatt_cost=None,
                )

            return Response(
                stream_with_context(generate()),
                content_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",  # disable nginx proxy buffering
                },
            )

        # ── NON-STREAMING ──────────────────────────────────────────────────
        resp = requests.post(
            f"{NEURALWATT_BASE_URL}/chat/completions",
            headers=headers,
            json=body,
            timeout=UPSTREAM_TIMEOUT,
        )
        data = resp.json()
        usage = data.get("usage", {}) or {}
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        neuralwatt_cost = None
        cost_data = data.get("cost", {}) or {}
        if cost_data:
            neuralwatt_cost = cost_data.get("request_cost_usd")

        track_usage(customer_api_key, requested_model, prompt_tokens, completion_tokens, neuralwatt_cost)
        return jsonify(data), resp.status_code

    except requests.exceptions.ConnectTimeout:
        return jsonify({"error": "Could not connect to Neuralwatt (connect timeout)"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── ADMIN ROUTES ──────────────────────────────────────────────────────────────

@app.route("/admin/usage", methods=["GET"])
def get_all_usage():
    return jsonify(usage_tracker)


@app.route("/admin/usage/<api_key>", methods=["GET"])
def get_usage_by_key(api_key):
    return jsonify(usage_tracker.get(api_key, {"error": "Key not found"}))


@app.route("/admin/summary", methods=["GET"])
def get_summary():
    total_revenue = sum(v["your_revenue_usd"] for v in usage_tracker.values())
    total_cost = sum(v["neuralwatt_cost_usd"] for v in usage_tracker.values())
    total_requests = sum(v["total_requests"] for v in usage_tracker.values())
    total_tokens = sum(v["total_tokens"] for v in usage_tracker.values())
    return jsonify({
        "total_customers": len(usage_tracker),
        "total_requests": total_requests,
        "total_tokens": total_tokens,
        "total_revenue_usd": round(total_revenue, 6),
        "total_cost_usd": round(total_cost, 6),
        "total_profit_usd": round(total_revenue - total_cost, 6),
        "gross_margin_pct": round(((total_revenue - total_cost) / total_revenue * 100) if total_revenue > 0 else 0, 2),
    })


@app.route("/admin/reset", methods=["POST"])
def reset_usage():
    usage_tracker.clear()
    return jsonify({"status": "reset"})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": datetime.utcnow().isoformat()})


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"""
+==========================================================+
|   Neuralwatt Proxy Server (hardened)                     |
|   Running at:  http://localhost:{PORT}                      |
|   Admin:       http://localhost:{PORT}/admin/summary        |
|   No upstream read-timeout: long generations won't chop. |
+==========================================================+

Model mappings:""")
    for k, v in MODEL_MAP.items():
        print(f"  {k:<35} -> {v}")
    print()
    # threaded=True so concurrent Cursor requests don't serialize. No debug reloader.
    # PRODUCTION: prefer a real server that supports streaming + concurrency, e.g.
    #   gunicorn -k gevent -w 4 --timeout 0 -b 0.0.0.0:4000 proxy:app
    # (`--timeout 0` disables gunicorn's worker timeout so long streams aren't killed;
    #  the gevent worker handles many simultaneous SSE connections.)
    # Also ensure any host/CDN in front (Cloudflare, nginx, serverless) does NOT
    # buffer responses and has no max-duration limit shorter than your longest call.
    app.run(host="0.0.0.0", port=PORT, threaded=True)
