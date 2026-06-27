"""
Neuralwatt Energy Pricing Test Server
- Runs on port 3000
- No caching (fresh context every request)
- Tracks energy cost and calculates effective cost per million tokens
- Uses GLM-5.2 via Neuralwatt API

Setup:
    pip install flask requests openai

Usage:
    1. Set your API key below (or use env var NEURALWATT_API_KEY)
    2. Run: python neuralwatt_test.py
    3. Open http://localhost:3000
"""

import os
import json
import time
import uuid
import secrets
from flask import Flask, request, jsonify, render_template_string, make_response
from openai import OpenAI

# ── CONFIG ────────────────────────────────────────────────────────────────────
NEURALWATT_API_KEY = "sk-478b6720e8dd77d9e5b60d6cde17398cf3edfac6b9b41fc35a5aa71f914d14a8"
NEURALWATT_BASE_URL = "https://api.neuralwatt.com/v1"  # update if different
MODEL = "glm-5.2"
PORT = 3000

# ── FLASK APP ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# In-memory stats tracker (resets on server restart)
stats = {
    "total_requests": 0,
    "total_tokens": 0,
    "total_prompt_tokens": 0,
    "total_completion_tokens": 0,
    "total_cost_usd": 0.0,
    "total_energy_kwh": 0.0,
    "requests": []
}

# ── OPENAI CLIENT (pointing at Neuralwatt) ────────────────────────────────────
client = OpenAI(
    api_key=NEURALWATT_API_KEY,
    base_url=NEURALWATT_BASE_URL,
)

# ── HTML UI ───────────────────────────────────────────────────────────────────
HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <title>Neuralwatt Zero-Cache Tester</title>
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"/>
  <meta http-equiv="Pragma" content="no-cache"/>
  <meta http-equiv="Expires" content="0"/>
  <style>
    body { font-family: monospace; max-width: 900px; margin: 40px auto; padding: 0 20px; background: #0f0f0f; color: #e0e0e0; }
    h1 { color: #ff6b35; }
    h2 { color: #aaa; font-size: 14px; margin-top: 30px; }
    textarea { width: 100%; height: 100px; background: #1a1a1a; color: #e0e0e0; border: 1px solid #333; padding: 10px; font-family: monospace; font-size: 13px; resize: vertical; }
    .btn-row { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
    button { background: #ff6b35; color: white; border: none; padding: 10px 24px; cursor: pointer; font-size: 14px; }
    button:hover { background: #e55a25; }
    .done-indicator { display: none; color: #4caf50; font-size: 20px; }
    .done-banner { display: none; margin-top: 12px; padding: 14px 20px; background: linear-gradient(135deg, #1b5e20, #2e7d32); border: 2px solid #4caf50; border-radius: 8px; text-align: center; font-weight: bold; font-size: 18px; color: #c8e6c9; letter-spacing: 1px; box-shadow: 0 0 20px rgba(76, 175, 80, 0.3); animation: pulse-glow 2s ease-in-out infinite; }
    .done-banner .check { font-size: 28px; display: block; margin-bottom: 4px; }
    @keyframes pulse-glow { 0%, 100% { box-shadow: 0 0 20px rgba(76, 175, 80, 0.3); } 50% { box-shadow: 0 0 40px rgba(76, 175, 80, 0.6); } }
    .response { background: #1a1a1a; border: 1px solid #333; margin-top: 16px; font-size: 13px; overflow: hidden; }
    .response-header { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; cursor: pointer; user-select: none; }
    .response-header:hover { background: #222; }
    .response-header .arrow { color: #888; font-size: 12px; transition: transform 0.2s; }
    .response-header .arrow.open { transform: rotate(90deg); }
    .response-body { display: none; padding: 0 14px 14px; white-space: pre-wrap; }
    .response-body.open { display: block; }
    .stats-grid { display: flex; justify-content: center; margin-top: 16px; }
    .stat-box { background: #1a1a1a; border: 1px solid #333; padding: 14px; text-align: center; }
    .stat-value { font-size: 22px; color: #ff6b35; font-weight: bold; }
    .stat-label { font-size: 11px; color: #888; margin-top: 4px; }
    #loading { display: none; color: #ff6b35; margin-top: 8px; }
  </style>
</head>
<body>
  <h1>⚡ Neuralwatt Zero-Cache Tester</h1>

  <h2>SEND A REQUEST</h2>
  <textarea id="prompt" placeholder="Type your prompt here..."></textarea>
  <br/>
  <div class="btn-row">
    <button type="button" onclick="sendRequest()">Send Request →</button>
    <span class="done-indicator" id="done-indicator">&#10003;</span>
  </div>
  <div id="loading">⏳ Waiting for response...</div>

  <div id="done-banner" class="done-banner">
    <span class="check">&#10003;</span>
    DONE — READY FOR THE NEXT PROMPT
  </div>

  <div id="response-box" class="response" style="display:none">
    <div class="response-header" onclick="toggleResponse()">
      <span>AI Response</span>
      <span class="arrow" id="response-arrow">&#9654;</span>
    </div>
    <div class="response-body" id="response-body"></div>
  </div>

  <h2>SESSION STATS</h2>
  <div class="stats-grid">
    <div class="stat-box">
      <div class="stat-value" id="s-requests">0</div>
      <div class="stat-label">Total Requests</div>
    </div>
  </div>

  <script>
    function toggleResponse() {
      const body = document.getElementById('response-body');
      const arrow = document.getElementById('response-arrow');
      body.classList.toggle('open');
      arrow.classList.toggle('open');
    }

    async function sendRequest() {
      const prompt = document.getElementById('prompt').value.trim();
      if (!prompt) return alert('Enter a prompt first.');

      // Hide done states when starting a new request
      document.getElementById('loading').style.display = 'block';
      document.getElementById('done-indicator').style.display = 'none';
      document.getElementById('done-banner').style.display = 'none';
      document.getElementById('response-box').style.display = 'none';

      const res = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-cache', 'Pragma': 'no-cache' },
        body: JSON.stringify({ prompt })
      });

      const data = await res.json();
      document.getElementById('loading').style.display = 'none';

      if (data.error) {
        document.getElementById('response-box').style.display = 'block';
        document.getElementById('response-body').textContent = '❌ Error: ' + data.error;
        document.getElementById('response-body').classList.add('open');
        document.getElementById('response-arrow').classList.add('open');
        return;
      }

      // Show response in collapsible box
      document.getElementById('response-box').style.display = 'block';
      document.getElementById('response-body').textContent = data.reply;
      document.getElementById('response-body').classList.remove('open');
      document.getElementById('response-arrow').classList.remove('open');

      // Show done checkmark + prominent banner — stays until next request
      document.getElementById('done-indicator').style.display = 'inline';
      document.getElementById('done-banner').style.display = 'block';

      // Update stats
      document.getElementById('s-requests').textContent = data.stats.total_requests;

      document.getElementById('prompt').value = '';
    }
  </script>
</body>
</html>
"""

# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    resp = make_response(render_template_string(HTML))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/chat", methods=["POST"])
def chat():
    body = request.get_json()
    prompt = body.get("prompt", "").strip()
    if not prompt:
        resp = make_response(jsonify({"error": "No prompt provided"}), 400)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

    try:
        # Fresh messages array every time = zero caching
        # Add random nonce to force unique inference
        nonce = str(uuid.uuid4())
        messages = [
            {
                "role": "system",
                "content": f"You are a helpful assistant. Answer concisely. [nonce: {nonce}]"
            },
            {
                "role": "user",
                "content": prompt
            }
        ]

        # Make the API call — no caching whatsoever
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            seed=secrets.randbits(31),           # Random seed = no cached result possible
            temperature=0.7 + (secrets.randbits(8) / 1000),  # Slight jitter to bust any cache
            extra_headers={
                "X-No-Cache": "true",
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache"
            }
        )

        # Extract response text
        reply = response.choices[0].message.content

        # Extract token usage
        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        total_tokens = usage.total_tokens if usage else 0

        # Extract energy + cost from response if available
        energy_kwh = None
        cost_usd = None

        raw = response.model_extra or {}
        energy_data = raw.get("energy", {})
        cost_data = raw.get("cost", {})

        if isinstance(energy_data, dict) and energy_data.get("measurement_available", True):
            energy_kwh = energy_data.get("energy_kwh")
        if isinstance(cost_data, dict):
            cost_usd = cost_data.get("request_cost_usd")

        # Update session stats
        stats["total_requests"] += 1
        stats["total_tokens"] += total_tokens
        stats["total_prompt_tokens"] += prompt_tokens
        stats["total_completion_tokens"] += completion_tokens
        if cost_usd is not None:
            stats["total_cost_usd"] += cost_usd
        if energy_kwh is not None:
            stats["total_energy_kwh"] += energy_kwh

        # Calculate effective cost per million tokens
        cost_per_million = None
        if stats["total_tokens"] > 0 and stats["total_cost_usd"] > 0:
            cost_per_million = (stats["total_cost_usd"] / stats["total_tokens"]) * 1_000_000

        resp = make_response(jsonify({
            "reply": reply,
            "request": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "energy_kwh": energy_kwh,
                "cost_usd": cost_usd,
            },
            "stats": {
                **stats,
                "cost_per_million": cost_per_million
            }
        }))
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

    except Exception as e:
        resp = make_response(jsonify({"error": str(e)}), 500)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp


@app.route("/stats")
def get_stats():
    """JSON endpoint for raw stats"""
    cost_per_million = None
    if stats["total_tokens"] > 0 and stats["total_cost_usd"] > 0:
        cost_per_million = (stats["total_cost_usd"] / stats["total_tokens"]) * 1_000_000
    resp = make_response(jsonify({**stats, "cost_per_million": cost_per_million}))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/reset", methods=["POST"])
def reset_stats():
    """Reset session stats"""
    stats.update({
        "total_requests": 0, "total_tokens": 0,
        "total_prompt_tokens": 0, "total_completion_tokens": 0,
        "total_cost_usd": 0.0, "total_energy_kwh": 0.0, "requests": []
    })
    resp = make_response(jsonify({"status": "reset"}))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"""
+{'='*42}+
|   Neuralwatt Zero-Cache Tester               |
|   Model: {MODEL:<35} |
|   Running at: http://localhost:{PORT}           |
+{'='*42}+
    """)
    app.run(port=PORT, debug=False)
