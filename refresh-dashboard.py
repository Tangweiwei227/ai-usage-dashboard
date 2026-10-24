#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh-dashboard.py — generate / update an AI usage dashboard from your
Claude Code session logs (via ccusage).

Reads template.html, injects freshly-computed data into the DATA block,
and writes a standalone HTML file. Cross-platform, no hardcoded paths.

Usage:
    python3 refresh-dashboard.py
    python3 refresh-dashboard.py --infer-prices          # back out your effective unit prices
    python3 refresh-dashboard.py --claude-dir /path/.claude
    python3 refresh-dashboard.py --output ~/Desktop/my-usage.html
    python3 refresh-dashboard.py --rate 7.2

Requirements: python3 + npx (ccusage is fetched on demand via `npx -y ccusage`).
Currently supports Claude Code logs only (that's what ccusage parses).
"""
import json, os, glob, re, subprocess, sys, argparse, datetime
from pathlib import Path
from collections import defaultdict

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / 'template.html'
MARK_START = '/* ===DASHBOARD-DATA-START=== */'
MARK_END   = '/* ===DASHBOARD-DATA-END=== */'

# Official published list prices, $ per million tokens: [input, output, cache_write, cache_read].
# Matched by substring on the model name. Unknown models fall back to inferred prices.
OFFICIAL_PRICES = [
    ('opus',   [15.0, 75.0, 18.75, 1.5]),
    ('sonnet', [3.0,  15.0, 3.75,  0.3]),
    ('haiku',  [1.0,  5.0,  1.25,  0.1]),
    ('gpt',    [1.25, 10.0, 0.0,   0.125]),
    ('codex',  [1.25, 10.0, 0.0,   0.125]),
]

# Standard input:output:cache ratios per family, used to reverse-engineer
# per-category unit prices from a single recorded cost figure.
def ratios(model):
    n = model.lower()
    if n.startswith('claude'):                  return dict(out=5,  cw=1.25, cr=0.1)   # Anthropic
    if 'gpt' in n or 'codex' in n:              return dict(out=8,  cw=0,    cr=0.1)   # OpenAI
    return dict(out=8, cw=0, cr=0.1)                                                  # default

def official_price(model):
    n = model.lower()
    for key, p in OFFICIAL_PRICES:
        if key in n:
            return list(p)
    return None

def normalize_agent_name(name):
    if not name:
        return ''
    s = str(name).strip()
    if not s:
        return ''
    low = s.lower()
    if low in ('claude code', 'claude'):
        return 'Claude'
    if low in ('codex', 'openai', 'chatgpt'):
        return 'Codex'
    return s

def guess_agent_from_model(model):
    n = (model or '').lower()
    if 'claude' in n:
        return 'Claude'
    if 'codex' in n or 'gpt' in n:
        return 'Codex'
    return 'Other'

def extract_agent_name(row, model_name=''):
    for k in ('agentName', 'agent', 'toolName', 'tool', 'source'):
        a = normalize_agent_name(row.get(k))
        if a:
            return a
    return guess_agent_from_model(model_name)

def decode_project(dirname):
    """ccusage/Claude encodes a project's cwd by replacing path separators (and
    other non-alphanumerics) with '-'. We can't perfectly recover the original,
    but we restore a readable tail: last 2 path-ish segments. CJK/space-only
    paths collapse to dashes, so fall back to the raw encoded name."""
    s = dirname.lstrip('-')
    if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-', dirname):   # bare UUID session dir
        return 'session ' + dirname[:8]
    parts = [p for p in re.split(r'-+', s) if p]
    if not parts:
        return dirname[:40]
    return '/'.join(parts[-2:])

def run_ccusage(args):
    cmd = ['npx', '-y', 'ccusage@latest'] + args + ['--json']
    print('  $', ' '.join(cmd))
    try:
        out = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        sys.exit('Error: `npx` not found. Install Node.js (https://nodejs.org) to use ccusage.')
    if out.returncode != 0 or not out.stdout.strip():
        sys.exit('ccusage failed:\n' + (out.stderr or '(empty output)'))
    return json.loads(out.stdout)

def main():
    ap = argparse.ArgumentParser(description='Generate the AI usage dashboard from Claude Code logs.')
    ap.add_argument('--claude-dir', help='Path to your .claude directory (default: ~/.claude or %%USERPROFILE%%\\.claude)')
    ap.add_argument('--output', help='Output HTML path (default: ./usage-dashboard.html)')
    ap.add_argument('--rate', type=float, default=7.15, help='Default USD→CNY rate shown in UI (default 7.15)')
    ap.add_argument('--infer-prices', action='store_true',
                    help='Reverse-engineer your effective unit prices from recorded cost '
                         '(use this if you pay via a discounted relay). Default uses official list prices.')
    args = ap.parse_args()

    claude_dir = Path(args.claude_dir).expanduser() if args.claude_dir else (Path.home() / '.claude')
    output = Path(args.output).expanduser() if args.output else (HERE / 'usage-dashboard.html')
    projects_dir = claude_dir / 'projects'

    if not TEMPLATE.exists():
        sys.exit(f'template.html not found at {TEMPLATE}')

    # ccusage reads from CLAUDE_CONFIG_DIR if set; honour --claude-dir the same way.
    if args.claude_dir:
        os.environ['CLAUDE_CONFIG_DIR'] = str(claude_dir)

    print('> Fetching usage via ccusage …')
    daily_raw   = run_ccusage([])
    monthly_raw = run_ccusage(['monthly'])
    session_raw = run_ccusage(['session'])

    # ---- per-model 4-category token totals + cost ----
    M = defaultdict(lambda: [0, 0, 0, 0, 0.0])   # in, out, cw, cr, cost
    MA = defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0, 0.0]))   # agent -> model -> [in,out,cw,cr,cost]
    daily_cost_by_agent = defaultdict(lambda: defaultdict(float))       # agent -> date -> cost
    for r in daily_raw.get('daily', []):
        day = r.get('period')
        for b in r.get('modelBreakdowns', []):
            model_name = b.get('modelName', '(unknown)')
            agent = extract_agent_name(b, model_name)
            v = M[model_name]
            v[0] += b.get('inputTokens', 0);          v[1] += b.get('outputTokens', 0)
            v[2] += b.get('cacheCreationTokens', 0);  v[3] += b.get('cacheReadTokens', 0)
            v[4] += b.get('cost', 0) or 0
            va = MA[agent][model_name]
            va[0] += b.get('inputTokens', 0);          va[1] += b.get('outputTokens', 0)
            va[2] += b.get('cacheCreationTokens', 0);  va[3] += b.get('cacheReadTokens', 0)
            va[4] += b.get('cost', 0) or 0
            if day:
                daily_cost_by_agent[agent][day] += b.get('cost', 0) or 0
    if not M:
        sys.exit('No usage found. Is this the right --claude-dir? Try running `npx ccusage` directly.')
    model_rows = sorted(M.items(), key=lambda x: -x[1][4])

    total_cost   = sum(v[4] for _, v in model_rows)
    total_tokens = sum(sum(v[:4]) for _, v in model_rows)

    # ---- monthly ----
    months  = [mm['period'] for mm in monthly_raw.get('monthly', [])]
    monthly = [{"m": mm['period'], "cost": round(mm['totalCost'], 2), "tok": mm['totalTokens']}
               for mm in monthly_raw.get('monthly', [])]

    # ---- month x model tokens (top 6 + Other) ----
    tok_mm = defaultdict(lambda: {mo: 0 for mo in months})
    tok_mm_agent = defaultdict(lambda: defaultdict(lambda: {mo: 0 for mo in months}))
    monthly_cost_by_agent = defaultdict(lambda: defaultdict(float))     # agent -> month -> cost
    tok_kind_by_agent = defaultdict(lambda: {"input": 0, "output": 0, "cacheWrite": 0, "cacheRead": 0})
    for mm in monthly_raw.get('monthly', []):
        month = mm.get('period')
        for b in mm.get('modelBreakdowns', []):
            model_name = b.get('modelName', '(unknown)')
            agent = extract_agent_name(b, model_name)
            tk = sum(b.get(k, 0) for k in ('inputTokens', 'outputTokens', 'cacheCreationTokens', 'cacheReadTokens'))
            tok_mm[model_name][month] += tk
            tok_mm_agent[agent][model_name][month] += tk
            monthly_cost_by_agent[agent][month] += b.get('cost', 0) or 0
            tok_kind_by_agent[agent]["input"] += b.get('inputTokens', 0)
            tok_kind_by_agent[agent]["output"] += b.get('outputTokens', 0)
            tok_kind_by_agent[agent]["cacheWrite"] += b.get('cacheCreationTokens', 0)
            tok_kind_by_agent[agent]["cacheRead"] += b.get('cacheReadTokens', 0)
    tok_sorted = sorted(tok_mm.items(), key=lambda x: -sum(x[1].values()))
    tokByModel = [{"n": mdl, "d": [mmv[mo] for mo in months]} for mdl, mmv in tok_sorted[:6]]
    if len(tok_sorted) > 6:
        other = [0] * len(months)
        for _, mmv in tok_sorted[6:]:
            for i, mo in enumerate(months):
                other[i] += mmv[mo]
        tokByModel.append({"n": "Other", "d": other})

    # ---- daily ----
    daily = [{"date": r['period'], "cost": round(r['totalCost'], 3)} for r in daily_raw.get('daily', [])]

    # ---- token kinds ----
    T = monthly_raw['totals']
    tokKind = {"input": T['inputTokens'], "output": T['outputTokens'],
               "cacheWrite": T['cacheCreationTokens'], "cacheRead": T['cacheReadTokens']}

    # ---- per project (map session UUID -> project dir) ----
    cost_by_uuid, tok_by_uuid = {}, {}
    agent_by_uuid = {}
    for s in session_raw.get('session', []):
        cost_by_uuid[s['period']] = s['totalCost']
        tok_by_uuid[s['period']]  = s['totalTokens']
        agent_by_uuid[s['period']] = extract_agent_name(s, s.get('modelName', ''))
    agg = defaultdict(lambda: [0.0, 0, 0])   # cost, tok, sessions
    agg_agent = defaultdict(lambda: defaultdict(lambda: [0.0, 0, 0]))   # agent -> project -> [cost,tok,sessions]
    if projects_dir.is_dir():
        for d in os.listdir(projects_dir):
            pdir = projects_dir / d
            if not pdir.is_dir():
                continue
            for f in glob.glob(str(pdir / '*.jsonl')):
                u = os.path.basename(f)[:-6]
                if u in cost_by_uuid:
                    name = decode_project(d)
                    agg[name][0] += cost_by_uuid[u]
                    agg[name][1] += tok_by_uuid[u]
                    agg[name][2] += 1
                    agent = agent_by_uuid.get(u, 'Other')
                    agg_agent[agent][name][0] += cost_by_uuid[u]
                    agg_agent[agent][name][1] += tok_by_uuid[u]
                    agg_agent[agent][name][2] += 1
    projects = sorted(
        [{"n": n, "s": v[2], "t": v[1], "c": round(v[0], 2)} for n, v in agg.items() if v[0] >= 0.01],
        key=lambda x: -x['c'])
    if not projects:   # session->dir mapping unavailable; degrade gracefully
        projects = [{"n": "(all sessions)", "s": len(cost_by_uuid),
                     "t": total_tokens, "c": round(total_cost, 2)}]

    # ---- by-agent slices ----
    agent_costs = {a: sum(v[4] for v in ms.values()) for a, ms in MA.items()}
    agents = [a for a, _ in sorted(agent_costs.items(), key=lambda x: -x[1])]

    by_agent = {}
    for agent in agents:
        rows = sorted(MA[agent].items(), key=lambda x: -x[1][4])
        a_total_cost = sum(v[4] for _, v in rows)
        a_total_tok = sum(sum(v[:4]) for _, v in rows)
        a_monthly = [{"m": mo, "cost": round(monthly_cost_by_agent[agent].get(mo, 0.0), 2),
                      "tok": sum(mmv.get(mo, 0) for mmv in tok_mm_agent[agent].values())} for mo in months]
        a_tok_sorted = sorted(tok_mm_agent[agent].items(), key=lambda x: -sum(x[1].values()))
        a_tok_by_model = [{"n": mdl, "d": [mmv[mo] for mo in months], "a": agent} for mdl, mmv in a_tok_sorted[:6]]
        if len(a_tok_sorted) > 6:
            other = [0] * len(months)
            for _, mmv in a_tok_sorted[6:]:
                for i, mo in enumerate(months):
                    other[i] += mmv[mo]
            a_tok_by_model.append({"n": "Other", "d": other, "a": agent})
        a_projects = sorted(
            [{"n": n, "s": v[2], "t": v[1], "c": round(v[0], 2), "a": agent}
             for n, v in agg_agent[agent].items() if v[0] >= 0.01],
            key=lambda x: -x['c']
        )
        if not a_projects:
            a_projects = [{"n": "(all sessions)", "s": 0, "t": 0, "c": 0.0, "a": agent}]
        by_agent[agent] = {
            "totals": {"cost": round(a_total_cost, 2), "tokens": a_total_tok},
            "models": [{"n": m, "c": round(v[4], 2), "t": sum(v[:4]), "a": agent} for m, v in rows],
            "monthly": a_monthly,
            "daily": [{"date": d['date'], "cost": round(daily_cost_by_agent[agent].get(d['date'], 0.0), 3)} for d in daily],
            "projects": a_projects,
            "tokByModel": a_tok_by_model,
            "tokKind": tok_kind_by_agent[agent],
            "defaultPrices": [],
        }
        for m, v in rows:
            R = ratios(m)
            denom = v[0] + R['out'] * v[1] + R['cw'] * v[2] + R['cr'] * v[3]
            pin = v[4] * 1e6 / denom if denom else 0
            inferred = [round(pin, 3), round(pin * R['out'], 3), round(pin * R['cw'], 3), round(pin * R['cr'], 3)]
            p = inferred if args.infer_prices else (official_price(m) or inferred)
            by_agent[agent]["defaultPrices"].append({"n": m, "a": agent, "in": v[0], "out": v[1], "cw": v[2], "cr": v[3],
                                                     "rec": round(v[4], 2), "p": p})

    # enrich global rows with agent detail
    models = []
    default_prices = []
    for agent in agents:
        for m, v in sorted(MA[agent].items(), key=lambda x: -x[1][4]):
            models.append({"n": m, "c": round(v[4], 2), "t": sum(v[:4]), "a": agent})
            R = ratios(m)
            denom = v[0] + R['out'] * v[1] + R['cw'] * v[2] + R['cr'] * v[3]
            pin = v[4] * 1e6 / denom if denom else 0
            inferred = [round(pin, 3), round(pin * R['out'], 3), round(pin * R['cw'], 3), round(pin * R['cr'], 3)]
            p = inferred if args.infer_prices else (official_price(m) or inferred)
            default_prices.append({"n": m, "a": agent, "in": v[0], "out": v[1], "cw": v[2], "cr": v[3],
                                   "rec": round(v[4], 2), "p": p})

    tok_by_agent = [{"n": a, "d": [sum(mmv.get(mo, 0) for mmv in tok_mm_agent[a].values()) for mo in months]}
                    for a in agents]
    month_by_agent = [{"n": a, "d": [round(monthly_cost_by_agent[a].get(mo, 0.0), 2) for mo in months]}
                      for a in agents]

    projects = [{"n": p["n"], "s": p["s"], "t": p["t"], "c": p["c"], "a": "All"} for p in projects]

    meta = {
        "start": daily[0]['date'] if daily else '',
        "end":   daily[-1]['date'] if daily else '',
        "activeDays": len(daily),
        "generated": datetime.date.today().isoformat(),
        "demo": False,
        "priceMode": "inferred" if args.infer_prices else "official",
        "defaultRate": args.rate,
    }

    data = {
        "meta": meta,
        "totals": {"cost": round(total_cost, 2), "tokens": total_tokens},
        "months": months,
        "models": models,
        "monthly": monthly,
        "daily": daily,
        "projects": projects,
        "tokByModel": tokByModel,
        "tokByAgent": tok_by_agent,
        "monthByAgent": month_by_agent,
        "tokKind": tokKind,
        "defaultPrices": default_prices,
        "agents": agents,
        "byAgent": by_agent,
    }

    block = MARK_START + '\nconst DATA = ' + json.dumps(data, ensure_ascii=False) + ';\n' + MARK_END
    html = TEMPLATE.read_text(encoding='utf-8')
    pat = re.compile(re.escape(MARK_START) + r'.*?' + re.escape(MARK_END), re.S)
    if not pat.search(html):
        sys.exit('DATA markers not found in template.html.')
    # also drop the default UI rate into the rate input default
    html = pat.sub(lambda _: block, html)
    output.write_text(html, encoding='utf-8')

    print('\n✓ Wrote', output)
    print(f'  Range {meta["start"]} → {meta["end"]} · {meta["activeDays"]} active days')
    print(f'  Total cost ${data["totals"]["cost"]:.2f} · {total_tokens:,} tokens')
    print(f'  {len(models)} models · {len(projects)} projects · price mode: {meta["priceMode"]}')
    print('  Open the HTML file in your browser to view.')

if __name__ == '__main__':
    main()
