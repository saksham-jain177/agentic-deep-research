"""Cost Dashboard - aggregate per-run cost data and render a static HTML view.

Builds on cost_estimator's layered pricing: callers record token usage per
pipeline stage (research / draft / verification / contradiction), this
module aggregates it into a run-summary JSON and generates a single-file
HTML dashboard (inline JS/CSS, no CDN or server dependencies) that reads
the JSON.

Usage:
    tracker = CostRunTracker(run_id="run-1", model="gpt-4o-mini")
    tracker.record("draft", input_tokens=1200, output_tokens=800)
    summary = tracker.build_summary()          # dict, JSON-schema'd
    write_run_summary(summary, "run-summary.json")
    write_dashboard(summary, "dashboard.html") # or omit summary to embed none
"""

import json
import logging
from typing import Dict, List, Any

import cost_estimator

logging.basicConfig(
    filename="research_agent.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

STAGES = ("research", "draft", "verification", "contradiction")

SCHEMA_VERSION = 1


class CostRunTracker:
    """Accumulates per-stage, per-model token usage for one research run.

    Token counts are recorded by the caller (from LLM responses or
    cost_estimator.count_text_tokens estimates); dollar costs are computed
    with the existing layered estimator logic.
    """

    def __init__(self, run_id: str, deep_research: bool = False,
                 query: str = ""):
        self.run_id = run_id
        self.deep_research = deep_research
        self.query = query
        # stage -> model -> {"input_tokens": int, "output_tokens": int}
        self._usage: Dict[str, Dict[str, Dict[str, int]]] = {
            stage: {} for stage in STAGES
        }

    def record(self, stage: str, input_tokens: int, output_tokens: int,
               model: str = None) -> None:
        """Add one stage's token usage. Unknown stages raise ValueError."""
        if stage not in STAGES:
            raise ValueError(
                f"Unknown stage {stage!r}; expected one of {STAGES}")
        model = model or os_model_default()
        bucket = self._usage[stage].setdefault(
            model, {"input_tokens": 0, "output_tokens": 0})
        bucket["input_tokens"] += max(int(input_tokens or 0), 0)
        bucket["output_tokens"] += max(int(output_tokens or 0), 0)

    def _stage_cost(self, model: str, input_tokens: int,
                    output_tokens: int):
        """USD cost via cost_estimator's layered heuristic pricing path.

        Uses the fallback per-1K rates directly so the dashboard is fully
        deterministic offline; genai-prices/tokencost layers remain
        available through TokenEstimator when richer catalogs are needed.
        """
        rate_in = cost_estimator.FALLBACK_PRICING["input"]
        rate_out = cost_estimator.FALLBACK_PRICING["output"]
        return round((input_tokens / 1000) * rate_in
                     + (output_tokens / 1000) * rate_out, 6)

    def build_summary(self) -> Dict[str, Any]:
        """Aggregate recorded usage into the run-summary dict."""
        stages_out = {}
        total_cost = 0.0
        total_in = 0
        total_out = 0

        for stage in STAGES:
            models_out = []
            stage_in = stage_out_toks = 0
            stage_cost = 0.0
            for model, toks in sorted(self._usage[stage].items()):
                cost = self._stage_cost(model, toks["input_tokens"],
                                        toks["output_tokens"])
                models_out.append({
                    "model": model,
                    "input_tokens": toks["input_tokens"],
                    "output_tokens": toks["output_tokens"],
                    "cost_usd": cost,
                })
                stage_in += toks["input_tokens"]
                stage_out_toks += toks["output_tokens"]
                stage_cost += cost
            stages_out[stage] = {
                "models": models_out,
                "input_tokens": stage_in,
                "output_tokens": stage_out_toks,
                "cost_usd": round(stage_cost, 6),
            }
            total_cost += stage_cost
            total_in += stage_in
            total_out += stage_out_toks

        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "deep_research": self.deep_research,
            "query": self.query,
            "stages": stages_out,
            "totals": {
                "input_tokens": total_in,
                "output_tokens": total_out,
                "total_tokens": total_in + total_out,
                "cost_usd": round(total_cost, 6),
            },
        }


def os_model_default() -> str:
    """Best-effort default model label from env (same vars as draft_agent)."""
    import os
    return (os.getenv("OPENROUTER_MODEL") or os.getenv("OPENAI_MODEL")
            or "unknown")


def write_run_summary(summary: Dict[str, Any], path: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    logging.info(f"Run summary written to {path}")
    return path


# --- HTML dashboard ----------------------------------------------------------

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cost Dashboard — {run_id}</title>
<style>
{css}
</style>
</head>
<body>
<h1>LLM Run Cost Dashboard</h1>
<div id="app">Loading…</div>
<script id="run-data" type="application/json">{data}</script>
<script>
{js}
</script>
</body>
</html>
"""

_CSS = """
body { font-family: -apple-system, Segoe UI, sans-serif; margin: 2rem;
       background: #fafafa; color: #222; }
h1 { font-size: 1.4rem; }
.cards { display: flex; gap: 1rem; margin: 1rem 0 2rem; flex-wrap: wrap; }
.card { background: #fff; border: 1px solid #e2e2e2; border-radius: 8px;
        padding: 1rem 1.5rem; min-width: 140px; }
.card .value { font-size: 1.6rem; font-weight: 600; }
.card .label { color: #666; font-size: .85rem; }
table { border-collapse: collapse; width: 100%; background: #fff;
        border: 1px solid #e2e2e2; }
th, td { text-align: left; padding: .5rem .75rem;
         border-bottom: 1px solid #eee; }
th { background: #f0f0f0; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
tr.stage-total td { font-weight: 600; background: #fcfcfc; }
.bar-chart { display: flex; align-items: flex-end; gap: 1.5rem;
             height: 180px; margin: 1.5rem 0; }
.bar-col { display: flex; flex-direction: column; align-items: center;
           justify-content: flex-end; height: 100%; }
.bar { width: 56px; background: #4a7dbe; border-radius: 4px 4px 0 0; }
.bar-label { margin-top: .35rem; font-size: .8rem; color: #444; }
.bar-value { font-size: .75rem; color: #666; }
"""

_JS = """
function fmtUsd(v) { return '$' + v.toFixed(4); }
function el(tag, attrs, children) {
  var e = document.createElement(tag);
  if (attrs) Object.keys(attrs).forEach(function(k){ e[k] = attrs[k]; });
  (children || []).forEach(function(c){ e.appendChild(c); });
  return e;
}
function render(data) {
  var app = document.getElementById('app');
  app.innerHTML = '';
  var t = data.totals;

  // Summary cards
  var cards = el('div', {className: 'cards'}, [
    card(t.cost_usd, 'Total estimated cost', fmtUsd),
    card(t.total_tokens.toLocaleString(), 'Total tokens'),
    card(t.input_tokens.toLocaleString(), 'Input tokens'),
    card(t.output_tokens.toLocaleString(), 'Output tokens')
  ]);
  app.appendChild(cards);

  // Bar chart of cost per stage
  var chart = el('div', {className: 'bar-chart'});
  var stageNames = Object.keys(data.stages);
  var maxCost = Math.max.apply(null, stageNames.map(function(s){
    return data.stages[s].cost_usd; }).concat([0.000001]));
  stageNames.forEach(function(s) {
    var st = data.stages[s];
    var h = Math.max(2, Math.round(st.cost_usd / maxCost * 150));
    chart.appendChild(el('div', {className: 'bar-col'}, [
      el('div', {className: 'bar-value', textContent: fmtUsd(st.cost_usd)}),
      el('div', {className: 'bar',
                 style: 'height:' + h + 'px'}),
      el('div', {className: 'bar-label', textContent: s})
    ]));
  });
  app.appendChild(el('h2', {textContent: 'Estimated cost per stage'}));
  app.appendChild(chart);

  // Per-stage table
  var table = el('table');
  table.innerHTML = '<tr><th>Stage</th><th>Model</th>' +
    '<th style="text-align:right">In tokens</th>' +
    '<th style="text-align:right">Out tokens</th>' +
    '<th style="text-align:right">Cost USD</th></tr>';
  stageNames.forEach(function(s) {
    var st = data.stages[s];
    st.models.forEach(function(m) {
      var row = document.createElement('tr');
      row.innerHTML =
        '<td>' + s + '</td><td>' + m.model + '</td>' +
        '<td class="num">' + m.input_tokens.toLocaleString() + '</td>' +
        '<td class="num">' + m.output_tokens.toLocaleString() + '</td>' +
        '<td class="num">' + fmtUsd(m.cost_usd) + '</td>';
      table.appendChild(row);
    });
    var tot = document.createElement('tr');
    tot.className = 'stage-total';
    tot.innerHTML =
      '<td colspan="2">' + s + ' — total</td>' +
      '<td class="num">' + st.input_tokens.toLocaleString() + '</td>' +
      '<td class="num">' + st.output_tokens.toLocaleString() + '</td>' +
      '<td class="num">' + fmtUsd(st.cost_usd) + '</td>';
    table.appendChild(tot);
  });
  app.appendChild(el('h2', {textContent: 'Breakdown'}));
  app.appendChild(table);

  function card(value, label, fmt) {
    return el('div', {className: 'card'}, [
      el('div', {className: 'value',
                 textContent: fmt ? fmt(value) : value}),
      el('div', {className: 'label', textContent: label})
    ]);
  }
}

(function main() {
  var raw = document.getElementById('run-data').textContent;
  try {
    render(JSON.parse(raw));
  } catch (e) {
    document.getElementById('app').textContent = 'Invalid run data: ' + e;
  }
})();
"""


def build_dashboard_html(summary: Dict[str, Any]) -> str:
    """Render the single-file dashboard HTML with embedded run JSON."""
    safe_json = json.dumps(summary).replace("</", "<\\/")
    return _HTML_TEMPLATE.format(
        run_id=summary.get("run_id", "unknown"),
        css=_CSS,
        js=_JS,
        data=safe_json,
    )


def write_dashboard(summary: Dict[str, Any], path: str) -> str:
    html = build_dashboard_html(summary)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def generate_from_usage(
    run_id: str,
    usage_rows: List[Dict[str, Any]],
    deep_research: bool = False,
    query: str = "",
) -> Dict[str, Any]:
    """Convenience builder: rows of {stage, input_tokens, output_tokens, model?}.

    Tokens may be supplied as ints or raw strings (estimated via
    cost_estimator.count_text_tokens).
    """
    tracker = CostRunTracker(run_id=run_id, deep_research=deep_research,
                             query=query)
    for row in usage_rows:
        in_tok = row.get("input_tokens", 0)
        out_tok = row.get("output_tokens", 0)
        if isinstance(in_tok, str):
            in_tok = cost_estimator.count_text_tokens(in_tok)
        if isinstance(out_tok, str):
            out_tok = cost_estimator.count_text_tokens(out_tok)
        tracker.record(row.get("stage"), in_tok, out_tok,
                       model=row.get("model"))
    return tracker.build_summary()
