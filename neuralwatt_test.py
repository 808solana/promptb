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
NEURALWATT_API_KEY = "sk-02026b28fd853228f52756d9372255d1a4d522f0e1c2b2cde2d17d380f434389"
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
    button { background: #ff6b35; color: white; border: none; padding: 10px 24px; cursor: pointer; font-size: 14px; margin-top: 8px; }
    button:hover { background: #e55a25; }
    .response { background: #1a1a1a; border: 1px solid #333; padding: 16px; margin-top: 16px; white-space: pre-wrap; font-size: 13px; }
    .stats-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 16px; }
    .stat-box { background: #1a1a1a; border: 1px solid #333; padding: 14px; text-align: center; }
    .stat-value { font-size: 22px; color: #ff6b35; font-weight: bold; }
    .stat-label { font-size: 11px; color: #888; margin-top: 4px; }
    .history { margin-top: 20px; }
    .history-row { border-bottom: 1px solid #222; padding: 8px 0; font-size: 12px; color: #aaa; }
    .pill { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; margin-left: 6px; }
    .green { background: #1a3a1a; color: #4caf50; }
    .orange { background: #3a2a1a; color: #ff9800; }
    #loading { display: none; color: #ff6b35; margin-top: 8px; }
  </style>
</head>
<body>
  <h1>⚡ Neuralwatt Zero-Cache Tester</h1>
  <p style="color:#888; font-size:13px;">Every request uses fresh context — no caching. This gives you worst-case energy cost per million tokens.</p>

  <h2>SEND A REQUEST</h2>
  <textarea id="prompt" placeholder="Type your prompt here..."></textarea>
  <br/>
  <button onclick="sendRequest()">Send Request →</button>
  <div id="loading">⏳ Waiting for response...</div>

  <div id="response-box" class="response" style="display:none"></div>

  <h2>SESSION STATS</h2>
  <div class="stats-grid">
    <div class="stat-box">
      <div class="stat-value" id="s-requests">0</div>
      <div class="stat-label">Total Requests</div>
    </div>
    <div class="stat-box">
      <div class="stat-value" id="s-tokens">0</div>
      <div class="stat-label">Total Tokens</div>
    </div>
    <div class="stat-box">
      <div class="stat-value" id="s-cost">$0.00</div>
      <div class="stat-label">Total Cost (USD)</div>
    </div>
    <div class="stat-box">
      <div class="stat-value" id="s-energy">0 Wh</div>
      <div class="stat-label">Energy Consumed</div>
    </div>
    <div class="stat-box">
      <div class="stat-value" id="s-cpm">—</div>
      <div class="stat-label">Effective Cost / 1M Tokens</div>
    </div>
    <div class="stat-box">
      <div class="stat-value" id="s-savings">—</div>
      <div class="stat-label">vs $5.00/M Token Rate</div>
    </div>
  </div>

  <h2>REQUEST HISTORY</h2>
  <div id="history" class="history"></div>

  <script>
    async function sendRequest() {
      const prompt = document.getElementById('prompt').value.trim();
      if (!prompt) return alert('Enter a prompt first.');

      document.getElementById('loading').style.display = 'block';
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
        document.getElementById('response-box').textContent = '❌ Error: ' + data.error;
        return;
      }

      document.getElementById('response-box').style.display = 'block';
      document.getElementById('response-box').textContent = data.reply;

      // Update stats
      document.getElementById('s-requests').textContent = data.stats.total_requests;
      document.getElementById('s-tokens').textContent = (data.stats.total_tokens / 1e6).toFixed(2) + 'M';
      document.getElementById('s-cost').textContent = '$' + data.stats.total_cost_usd.toFixed(4);
      document.getElementById('s-energy').textContent = (data.stats.total_energy_kwh * 1000).toFixed(4) + ' Wh';

      const cpm = data.stats.cost_per_million;
      document.getElementById('s-cpm').textContent = cpm ? '$' + cpm.toFixed(4) : '—';

      if (cpm) {
        const savings = (((5.00 - cpm) / 5.00) * 100).toFixed(1);
        document.getElementById('s-savings').textContent = savings + '% cheaper';
        document.getElementById('s-savings').style.color = savings > 0 ? '#4caf50' : '#f44336';
      }

      // Add to history
      const hist = document.getElementById('history');
      const row = document.createElement('div');
      row.className = 'history-row';
      row.innerHTML = `
        <strong>#${data.stats.total_requests}</strong>
        <span class="pill orange">${data.request.prompt_tokens} prompt tkns</span>
        <span class="pill orange">${data.request.completion_tokens} completion tkns</span>
        <span class="pill green">$${data.request.cost_usd ? data.request.cost_usd.toFixed(6) : 'N/A'}</span>
        <span class="pill green">${data.request.energy_kwh ? (data.request.energy_kwh * 1e6).toFixed(2) + ' mWh' : 'N/A'}</span>
        <br/><span style="color:#666; padding-left:4px">${prompt.substring(0, 80)}${prompt.length > 80 ? '...' : ''}</span>
      `;
      hist.prepend(row);

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
            max_tokens=1024,
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
╔══════════════════════════════════════════════╗
║   Neuralwatt Zero-Cache Tester               ║
║   Model: {MODEL:<35} ║
║   Running at: http://localhost:{PORT}           ║
╚══════════════════════════════════════════════╝
    """)
    app.run(port=PORT, debug=True)
