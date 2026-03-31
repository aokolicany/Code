#!/usr/bin/env python3
"""
Claude Usage Dashboard Generator

Parses local Claude Code session logs (~/.claude/) and optionally the Anthropic
Admin API to generate a self-contained HTML dashboard for the previous weekday.

Usage:
    python3 claude_usage_dashboard.py [YYYY-MM-DD]

Environment:
    ANTHROPIC_ADMIN_KEY  (optional) Admin API key for org-level usage data
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Model pricing (USD per 1M tokens, as of early 2026)
# ---------------------------------------------------------------------------
MODEL_PRICING = {
    "claude-opus-4-6":   {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50},
    "claude-sonnet-4-6": {"input":  3.00, "output": 15.00, "cache_write":  3.75, "cache_read": 0.30},
    "claude-haiku-4-5-20251001": {"input":  0.80, "output":  4.00, "cache_write":  1.00, "cache_read": 0.08},
    "claude-haiku-4-5":  {"input":  0.80, "output":  4.00, "cache_write":  1.00, "cache_read": 0.08},
}

HAIKU_MODEL = "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def get_previous_weekday(today: date | None = None) -> date:
    """Return the most recent weekday before today."""
    if today is None:
        today = date.today()
    d = today - timedelta(days=1)
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d -= timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
# Local session parsing
# ---------------------------------------------------------------------------

def _model_cost(model: str, input_t: int, output_t: int, cache_write_t: int, cache_read_t: int) -> float:
    prices = MODEL_PRICING.get(model) or MODEL_PRICING.get(model.split("-20")[0])
    if not prices:
        # Fallback to sonnet pricing for unknown models
        prices = MODEL_PRICING["claude-sonnet-4-6"]
    return (
        input_t       / 1_000_000 * prices["input"] +
        output_t      / 1_000_000 * prices["output"] +
        cache_write_t / 1_000_000 * prices["cache_write"] +
        cache_read_t  / 1_000_000 * prices["cache_read"]
    )


def parse_session_file(path: Path, target_date: date, is_subagent: bool = False) -> dict | None:
    """Parse one JSONL session file; return metrics dict or None if no records on target_date."""
    date_prefix = target_date.isoformat()
    records = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("timestamp", "").startswith(date_prefix):
                        records.append(obj)
                except json.JSONDecodeError:
                    continue
    except (IOError, PermissionError):
        return None

    if not records:
        return None

    # Project name: encode path dir is like "-home-user-Code"
    try:
        project_slug = path.parts[-3] if not is_subagent else path.parts[-4]
        project_name = project_slug.lstrip("-").replace("-", "/", 2)
    except IndexError:
        project_name = "unknown"

    metrics = {
        "session_id":          path.stem,
        "project":             project_name,
        "is_subagent":         is_subagent,
        "turns":               0,
        "models_used":         {},    # model -> count
        "input_tokens":        0,
        "output_tokens":       0,
        "cache_creation_tokens": 0,
        "cache_read_tokens":   0,
        "estimated_cost_usd":  0.0,
        "prompts":             [],    # list of user message strings
        "tool_calls":          0,
    }

    for rec in records:
        rtype = rec.get("type")

        if rtype == "assistant":
            msg   = rec.get("message", {})
            usage = msg.get("usage", {})
            model = msg.get("model", "unknown")

            inp   = usage.get("input_tokens", 0)
            out   = usage.get("output_tokens", 0)
            cw    = usage.get("cache_creation_input_tokens", 0)
            cr    = usage.get("cache_read_input_tokens", 0)

            metrics["turns"]               += 1
            metrics["input_tokens"]        += inp
            metrics["output_tokens"]       += out
            metrics["cache_creation_tokens"] += cw
            metrics["cache_read_tokens"]   += cr
            metrics["estimated_cost_usd"]  += _model_cost(model, inp, out, cw, cr)
            metrics["models_used"][model]   = metrics["models_used"].get(model, 0) + 1

            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    metrics["tool_calls"] += 1

        elif rtype == "user":
            content = rec.get("message", {}).get("content", "")
            if isinstance(content, str) and content.strip():
                metrics["prompts"].append(content.strip())
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        txt = block.get("text", "").strip()
                        if txt:
                            metrics["prompts"].append(txt)

    return metrics


def parse_local_sessions(target_date: date) -> list[dict]:
    """Glob all Claude Code project JSONL files and extract sessions for target_date."""
    sessions = []
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        return sessions

    # Main session files: projects/<project>/<sessionId>.jsonl
    for p in claude_projects.glob("*/*.jsonl"):
        s = parse_session_file(p, target_date, is_subagent=False)
        if s:
            sessions.append(s)

    # Subagent files: projects/<project>/<sessionId>/subagents/agent-*.jsonl
    for p in claude_projects.glob("*/*/subagents/agent-*.jsonl"):
        s = parse_session_file(p, target_date, is_subagent=True)
        if s:
            sessions.append(s)

    return sessions


# ---------------------------------------------------------------------------
# Anthropic Admin API (optional)
# ---------------------------------------------------------------------------

def fetch_anthropic_api_usage(target_date: date) -> dict | None:
    """
    Fetch org-level usage from the Anthropic Admin API.
    Returns a dict or None if ANTHROPIC_ADMIN_KEY is not set / request fails.
    """
    admin_key = os.environ.get("ANTHROPIC_ADMIN_KEY")
    if not admin_key:
        return None

    try:
        import urllib.request
        url = (
            "https://api.anthropic.com/v1/usage"
            f"?start_date={target_date.isoformat()}"
            f"&end_date={target_date.isoformat()}"
        )
        req = urllib.request.Request(
            url,
            headers={
                "x-api-key": admin_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        print(f"[warn] Anthropic Admin API call failed: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Efficiency analysis
# ---------------------------------------------------------------------------

def calculate_efficiency(sessions: list[dict]) -> dict:
    """Aggregate all sessions and compute efficiency metrics."""
    total_input   = sum(s["input_tokens"]          for s in sessions)
    total_output  = sum(s["output_tokens"]         for s in sessions)
    total_cw      = sum(s["cache_creation_tokens"] for s in sessions)
    total_cr      = sum(s["cache_read_tokens"]     for s in sessions)
    total_cost    = sum(s["estimated_cost_usd"]    for s in sessions)
    total_turns   = sum(s["turns"]                 for s in sessions)
    total_tools   = sum(s["tool_calls"]            for s in sessions)

    effective_input = total_input + total_cw + total_cr
    cache_hit_rate  = (total_cr / effective_input * 100) if effective_input > 0 else 0.0

    # Estimated savings: cache_read tokens cost ~90% less than fresh input
    haiku_input_price = MODEL_PRICING[HAIKU_MODEL]["input"]
    sonnet_input_price = MODEL_PRICING["claude-sonnet-4-6"]["input"]
    cache_savings_usd = total_cr / 1_000_000 * (sonnet_input_price - MODEL_PRICING["claude-sonnet-4-6"]["cache_read"])

    # Prompt verbosity
    all_prompts = []
    for s in sessions:
        for p in s.get("prompts", []):
            all_prompts.append({"text": p, "length": len(p), "session_id": s["session_id"], "project": s["project"]})
    avg_prompt_len = (sum(p["length"] for p in all_prompts) / len(all_prompts)) if all_prompts else 0
    verbose_threshold = max(avg_prompt_len * 2, 500)
    verbose_prompts   = sorted(
        [p for p in all_prompts if p["length"] > verbose_threshold],
        key=lambda x: x["length"], reverse=True
    )[:5]

    # Model usage aggregation across sessions
    model_totals: dict[str, dict] = {}
    for s in sessions:
        for model, count in s["models_used"].items():
            if model not in model_totals:
                model_totals[model] = {"turns": 0, "cost": 0.0}
            model_totals[model]["turns"] += count
            model_totals[model]["cost"]  += s["estimated_cost_usd"] * (count / max(s["turns"], 1))

    # Model selection recommendations: Sonnet/Opus used for tiny sessions
    model_suggestions = []
    for s in sessions:
        dominant = max(s["models_used"], key=s["models_used"].get) if s["models_used"] else ""
        if ("opus" in dominant or "sonnet" in dominant) and s["turns"] <= 3 and s["output_tokens"] < 1000:
            haiku_prices = MODEL_PRICING[HAIKU_MODEL]
            haiku_cost = _model_cost(
                HAIKU_MODEL,
                s["input_tokens"], s["output_tokens"],
                s["cache_creation_tokens"], s["cache_read_tokens"]
            )
            savings = max(0.0, s["estimated_cost_usd"] - haiku_cost)
            if savings > 0.0001:
                model_suggestions.append({
                    "session_id": s["session_id"][:8],
                    "project":    s["project"],
                    "model_used": dominant,
                    "suggestion": HAIKU_MODEL,
                    "turns":      s["turns"],
                    "output_tokens": s["output_tokens"],
                    "actual_cost": s["estimated_cost_usd"],
                    "haiku_cost":  haiku_cost,
                    "savings":     savings,
                })

    return {
        "total_sessions":      len(sessions),
        "total_turns":         total_turns,
        "total_tool_calls":    total_tools,
        "total_input_tokens":  total_input,
        "total_output_tokens": total_output,
        "total_cache_creation": total_cw,
        "total_cache_read":    total_cr,
        "total_cost_usd":      total_cost,
        "cache_hit_rate":      cache_hit_rate,
        "cache_savings_usd":   cache_savings_usd,
        "avg_prompt_length":   avg_prompt_len,
        "verbose_prompts":     verbose_prompts,
        "model_totals":        model_totals,
        "model_suggestions":   model_suggestions,
        "sessions_by_cost":    sorted(sessions, key=lambda x: x["estimated_cost_usd"], reverse=True),
    }


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def _fmt_cost(usd: float) -> str:
    if usd < 0.001:
        return f"${usd * 100:.4f}¢"
    return f"${usd:.4f}"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def _truncate(text: str, max_len: int = 120) -> str:
    text = text.replace("\n", " ").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    return text[:max_len] + ("…" if len(text) > max_len else "")


def generate_html(eff: dict, target_date: date, api_data: dict | None = None) -> str:
    generated_at = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---- Chart data ----
    sessions_by_cost = eff["sessions_by_cost"][:10]
    bar_labels = json.dumps([f"{s['session_id'][:8]} ({s['project'].split('/')[-1]})" for s in sessions_by_cost])
    bar_values = json.dumps([round(s["estimated_cost_usd"], 6) for s in sessions_by_cost])

    model_totals = eff["model_totals"]
    pie_labels   = json.dumps(list(model_totals.keys()))
    pie_values   = json.dumps([round(v["cost"], 6) for v in model_totals.values()])
    pie_colors   = json.dumps(["#7c3aed", "#2563eb", "#059669", "#d97706", "#dc2626"][:len(model_totals)])

    total_fresh = eff["total_input_tokens"]
    total_cw    = eff["total_cache_creation"]
    total_cr    = eff["total_cache_read"]
    donut_labels = json.dumps(["Fresh Input", "Cache Write", "Cache Read"])
    donut_values = json.dumps([total_fresh, total_cw, total_cr])

    cache_pct    = round(eff["cache_hit_rate"], 1)
    total_tokens = total_fresh + eff["total_output_tokens"] + total_cw + total_cr

    # ---- Session table rows ----
    session_rows = ""
    for s in eff["sessions_by_cost"]:
        badge = '<span class="badge sub">subagent</span>' if s["is_subagent"] else ""
        models = ", ".join(s["models_used"].keys()) or "—"
        session_rows += f"""
        <tr>
            <td><code>{s['session_id'][:12]}</code>{badge}</td>
            <td>{s['project'].split('/')[-1]}</td>
            <td>{s['turns']}</td>
            <td>{s['tool_calls']}</td>
            <td>{_fmt_tokens(s['input_tokens'])}</td>
            <td>{_fmt_tokens(s['output_tokens'])}</td>
            <td>{_fmt_tokens(s['cache_read_tokens'])}</td>
            <td>{_fmt_cost(s['estimated_cost_usd'])}</td>
            <td><small>{models}</small></td>
        </tr>"""

    # ---- Verbose prompts rows ----
    verbose_rows = ""
    for p in eff["verbose_prompts"]:
        verbose_rows += f"""
        <tr>
            <td><code>{p['session_id'][:8]}</code></td>
            <td>{p['project'].split('/')[-1]}</td>
            <td>{p['length']:,} chars</td>
            <td class="preview-cell">{_truncate(p['text'])}</td>
        </tr>"""
    if not verbose_rows:
        verbose_rows = '<tr><td colspan="4" class="empty">No verbose prompts detected — great job!</td></tr>'

    # ---- Model suggestion rows ----
    suggest_rows = ""
    for sg in eff["model_suggestions"]:
        suggest_rows += f"""
        <tr>
            <td><code>{sg['session_id']}</code></td>
            <td>{sg['project'].split('/')[-1]}</td>
            <td>{sg['model_used']}</td>
            <td>{sg['suggestion']}</td>
            <td>{sg['turns']}</td>
            <td>{_fmt_tokens(sg['output_tokens'])}</td>
            <td>{_fmt_cost(sg['actual_cost'])}</td>
            <td class="savings">~{_fmt_cost(sg['savings'])} saved</td>
        </tr>"""
    if not suggest_rows:
        suggest_rows = '<tr><td colspan="8" class="empty">Model selection looks optimal for the sessions detected.</td></tr>'

    # ---- API data section ----
    api_section = ""
    if api_data:
        api_section = f"""
        <section class="card">
            <h2>Anthropic Admin API — Org-Level Usage</h2>
            <pre class="api-raw">{json.dumps(api_data, indent=2)[:3000]}</pre>
        </section>"""

    no_data_banner = ""
    if eff["total_sessions"] == 0:
        no_data_banner = f"""
        <div class="no-data">
            No Claude Code sessions found for {target_date}. Make sure
            <code>~/.claude/projects/</code> is accessible and that sessions
            occurred on this date.
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Usage Dashboard — {target_date}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0f1117; --surface: #1a1d27; --border: #2d3148;
    --text: #e2e8f0; --muted: #8892a4; --accent: #7c3aed;
    --green: #22c55e; --yellow: #eab308; --red: #ef4444;
    --blue: #3b82f6;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 14px; padding: 24px; }}
  h1 {{ font-size: 1.6rem; font-weight: 700; margin-bottom: 4px; }}
  h2 {{ font-size: 1rem; font-weight: 600; margin-bottom: 16px; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; }}
  .meta {{ color: var(--muted); font-size: 0.82rem; margin-bottom: 28px; }}
  .grid-4 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 24px; }}
  .stat-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 18px; }}
  .stat-label {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; margin-bottom: 6px; }}
  .stat-value {{ font-size: 1.7rem; font-weight: 700; }}
  .stat-sub {{ font-size: 0.78rem; color: var(--muted); margin-top: 4px; }}
  .green {{ color: var(--green); }} .yellow {{ color: var(--yellow); }} .red {{ color: var(--red); }} .blue {{ color: var(--blue); }} .accent {{ color: var(--accent); }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ text-align: left; padding: 8px 12px; font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; border-bottom: 1px solid var(--border); }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #1f2235; vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #1f2235; }}
  code {{ font-family: "JetBrains Mono", monospace; font-size: 0.82rem; background: #252837; padding: 1px 5px; border-radius: 4px; }}
  pre.api-raw {{ background: #252837; padding: 16px; border-radius: 8px; overflow-x: auto; font-size: 0.78rem; color: var(--muted); }}
  .badge {{ font-size: 0.68rem; padding: 2px 6px; border-radius: 4px; margin-left: 6px; vertical-align: middle; }}
  .badge.sub {{ background: #1d2a3a; color: var(--blue); }}
  .preview-cell {{ max-width: 340px; color: var(--muted); font-size: 0.82rem; word-break: break-word; }}
  .savings {{ color: var(--green); font-weight: 600; }}
  .empty {{ color: var(--muted); text-align: center; padding: 20px; font-style: italic; }}
  .no-data {{ background: #2a1a1a; border: 1px solid #5a2020; border-radius: 10px; padding: 18px 22px; margin-bottom: 24px; color: #fca5a5; }}
  .cache-bar-wrap {{ background: #252837; border-radius: 8px; height: 14px; overflow: hidden; margin-top: 10px; }}
  .cache-bar {{ height: 100%; background: linear-gradient(90deg, var(--accent), var(--blue)); border-radius: 8px; transition: width .6s; }}
  .chart-wrap {{ position: relative; height: 260px; }}
  .chart-wrap-sm {{ position: relative; height: 220px; }}
  @media (max-width: 900px) {{ .grid-4 {{ grid-template-columns: 1fr 1fr; }} .grid-2 {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>

<h1>Claude Usage Dashboard</h1>
<p class="meta">Report date: <strong>{target_date}</strong> &nbsp;|&nbsp; Generated: {generated_at}</p>

{no_data_banner}

<!-- Summary cards -->
<div class="grid-4">
  <div class="stat-card">
    <div class="stat-label">Sessions</div>
    <div class="stat-value accent">{eff['total_sessions']}</div>
    <div class="stat-sub">{eff['total_turns']} turns · {eff['total_tool_calls']} tool calls</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Total Tokens</div>
    <div class="stat-value blue">{_fmt_tokens(total_tokens)}</div>
    <div class="stat-sub">{_fmt_tokens(eff['total_input_tokens'])} in · {_fmt_tokens(eff['total_output_tokens'])} out</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Estimated Cost</div>
    <div class="stat-value yellow">{_fmt_cost(eff['total_cost_usd'])}</div>
    <div class="stat-sub">~{_fmt_cost(eff['cache_savings_usd'])} saved by cache</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Cache Hit Rate</div>
    <div class="stat-value green">{cache_pct}%</div>
    <div class="cache-bar-wrap"><div class="cache-bar" style="width:{min(cache_pct,100)}%"></div></div>
  </div>
</div>

<!-- Token cost by session + model pie -->
<div class="grid-2">
  <div class="card">
    <h2>Cost by Session (top 10)</h2>
    <div class="chart-wrap-sm"><canvas id="barChart"></canvas></div>
  </div>
  <div class="card">
    <h2>Cost by Model</h2>
    <div class="chart-wrap-sm"><canvas id="pieChart"></canvas></div>
  </div>
</div>

<!-- Cache performance -->
<div class="card">
  <h2>Cache Performance</h2>
  <div class="grid-2">
    <div class="chart-wrap-sm"><canvas id="donutChart"></canvas></div>
    <div style="display:flex;flex-direction:column;gap:14px;justify-content:center;padding:10px">
      <div>
        <div class="stat-label">Cache Read Tokens</div>
        <div style="font-size:1.4rem;font-weight:700;color:var(--green)">{_fmt_tokens(total_cr)}</div>
        <div class="stat-sub">Context served from cache</div>
      </div>
      <div>
        <div class="stat-label">Cache Creation Tokens</div>
        <div style="font-size:1.4rem;font-weight:700;color:var(--blue)">{_fmt_tokens(total_cw)}</div>
        <div class="stat-sub">Written to prompt cache</div>
      </div>
      <div>
        <div class="stat-label">Estimated Cache Savings</div>
        <div style="font-size:1.4rem;font-weight:700;color:var(--yellow)">{_fmt_cost(eff['cache_savings_usd'])}</div>
        <div class="stat-sub">vs. re-sending as fresh input</div>
      </div>
    </div>
  </div>
</div>

<!-- Session table -->
<div class="card">
  <h2>All Sessions</h2>
  <div style="overflow-x:auto">
  <table>
    <thead><tr>
      <th>Session ID</th><th>Project</th><th>Turns</th><th>Tools</th>
      <th>Input</th><th>Output</th><th>Cache Read</th><th>Cost</th><th>Model(s)</th>
    </tr></thead>
    <tbody>{session_rows}</tbody>
  </table>
  </div>
</div>

<!-- Prompt verbosity -->
<div class="card">
  <h2>Prompt Verbosity</h2>
  <div style="margin-bottom:14px;color:var(--muted)">
    Average prompt length: <strong style="color:var(--text)">{int(eff['avg_prompt_length']):,} chars</strong>
    &nbsp;·&nbsp; Verbose threshold: <strong style="color:var(--text)">{int(max(eff['avg_prompt_length']*2, 500)):,} chars</strong>
    &nbsp;·&nbsp; Verbose prompts found: <strong style="color:var(--yellow)">{len(eff['verbose_prompts'])}</strong>
  </div>
  <table>
    <thead><tr><th>Session</th><th>Project</th><th>Length</th><th>Preview</th></tr></thead>
    <tbody>{verbose_rows}</tbody>
  </table>
</div>

<!-- Model selection recommendations -->
<div class="card">
  <h2>Model Selection Recommendations</h2>
  <div style="margin-bottom:14px;color:var(--muted)">Sessions using Sonnet/Opus with ≤3 turns and &lt;1K output tokens — Haiku may suffice.</div>
  <div style="overflow-x:auto">
  <table>
    <thead><tr>
      <th>Session</th><th>Project</th><th>Used</th><th>Suggested</th>
      <th>Turns</th><th>Output</th><th>Actual Cost</th><th>Potential Saving</th>
    </tr></thead>
    <tbody>{suggest_rows}</tbody>
  </table>
  </div>
</div>

{api_section}

<script>
Chart.defaults.color = '#8892a4';
Chart.defaults.borderColor = '#2d3148';

// Bar chart — cost by session
new Chart(document.getElementById('barChart'), {{
  type: 'bar',
  data: {{
    labels: {bar_labels},
    datasets: [{{ label: 'Cost (USD)', data: {bar_values},
      backgroundColor: 'rgba(124,58,237,0.7)', borderColor: '#7c3aed', borderWidth: 1, borderRadius: 4 }}]
  }},
  options: {{
    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ x: {{ ticks: {{ callback: v => '$' + v.toFixed(4) }} }}, y: {{ ticks: {{ font: {{ size: 10 }} }} }} }}
  }}
}});

// Pie chart — cost by model
new Chart(document.getElementById('pieChart'), {{
  type: 'doughnut',
  data: {{
    labels: {pie_labels},
    datasets: [{{ data: {pie_values}, backgroundColor: {pie_colors}, borderWidth: 2, borderColor: '#1a1d27' }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ font: {{ size: 11 }}, padding: 10 }} }},
      tooltip: {{ callbacks: {{ label: ctx => ctx.label + ': $' + ctx.parsed.toFixed(5) }} }}
    }}
  }}
}});

// Donut chart — token type breakdown
new Chart(document.getElementById('donutChart'), {{
  type: 'doughnut',
  data: {{
    labels: {donut_labels},
    datasets: [{{ data: {donut_values},
      backgroundColor: ['#2563eb','#7c3aed','#22c55e'], borderWidth: 2, borderColor: '#1a1d27' }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ font: {{ size: 11 }}, padding: 10 }} }},
      tooltip: {{ callbacks: {{ label: ctx => ctx.label + ': ' + (ctx.parsed/1000).toFixed(1) + 'K' }} }}
    }}
  }}
}});
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) > 1:
        try:
            target_date = date.fromisoformat(sys.argv[1])
        except ValueError:
            print(f"Invalid date: {sys.argv[1]}. Use YYYY-MM-DD.", file=sys.stderr)
            sys.exit(1)
    else:
        target_date = get_previous_weekday()

    print(f"Generating dashboard for {target_date}...")

    sessions  = parse_local_sessions(target_date)
    print(f"  Found {len(sessions)} session(s) in local logs.")

    api_data  = fetch_anthropic_api_usage(target_date)
    if api_data:
        print("  Fetched Anthropic Admin API data.")
    else:
        print("  Skipping Admin API (ANTHROPIC_ADMIN_KEY not set).")

    eff  = calculate_efficiency(sessions)
    html = generate_html(eff, target_date, api_data)

    out_dir = Path(__file__).parent / "dashboard_output"
    out_dir.mkdir(exist_ok=True)

    dated_file  = out_dir / f"claude_usage_{target_date}.html"
    latest_file = out_dir / "latest.html"

    dated_file.write_text(html, encoding="utf-8")
    latest_file.write_text(html, encoding="utf-8")

    print(f"  Dashboard saved: {dated_file}")
    print(f"  Latest link:     {latest_file}")


if __name__ == "__main__":
    main()
