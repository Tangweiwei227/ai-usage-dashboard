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

def run_ccusage(args, extra=None):
    cmd = ['npx', '-y', 'ccusage@latest'] + args + ['--json'] + (extra or [])
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
    env_extra = []
    if args.claude_dir:
        os.environ['CLAUDE_CONFIG_DIR'] = str(claude_dir)

    print('> Fetching usage via ccusage …')
    daily_raw   = run_ccusage([])
    monthly_raw = run_ccusage(['monthly'])
    session_raw = run_ccusage(['session'])

    # ---- per-model 4-category token totals + cost ----
    M = defaultdict(lambda: [0, 0, 0, 0, 0.0])   # in, out, cw, cr, cost
    for r in daily_raw.get('daily', []):
        for b in r.get('modelBreakdowns', []):
            v = M[b['modelName']]
            v[0] += b.get('inputTokens', 0);          v[1] += b.get('outputTokens', 0)
            v[2] += b.get('cacheCreationTokens', 0);  v[3] += b.get('cacheReadTokens', 0)
            v[4] += b.get('cost', 0) or 0
    if not M:
        sys.exit('No usage found. Is this the right --claude-dir? Try running `npx ccusage` directly.')
    model_rows = sorted(M.items(), key=lambda x: -x[1][4])

    total_cost   = sum(v[4] for _, v in model_rows)
    total_tokens = sum(sum(v[:4]) for _, v in model_rows)

    # ---- default prices (official, or inferred with --infer-prices) ----
    default_prices = []
    for m, v in model_rows:
        inn, out, cw, cr, cost = v
        R = ratios(m)
        denom = inn + R['out'] * out + R['cw'] * cw + R['cr'] * cr
        pin = cost * 1e6 / denom if denom else 0
        inferred = [round(pin, 3), round(pin * R['out'], 3), round(pin * R['cw'], 3), round(pin * R['cr'], 3)]
        if args.infer_prices:
            p = inferred
        else:
            p = official_price(m) or inferred   # unknown families fall back to inferred
        default_prices.append({"n": m, "in": inn, "out": out, "cw": cw, "cr": cr,
                               "rec": round(cost, 2), "p": p})

    models = [{"n": m, "c": round(v[4], 2), "t": sum(v[:4])} for m, v in model_rows]

    # ---- monthly ----
    months  = [mm['period'] for mm in monthly_raw.get('monthly', [])]
    monthly = [{"m": mm['period'], "cost": round(mm['totalCost'], 2), "tok": mm['totalTokens']}
               for mm in monthly_raw.get('monthly', [])]

    # ---- month x model tokens (top 6 + Other) ----
    tok_mm = defaultdict(lambda: {mo: 0 for mo in months})
    for mm in monthly_raw.get('monthly', []):
        for b in mm.get('modelBreakdowns', []):
            tk = sum(b.get(k, 0) for k in ('inputTokens', 'outputTokens', 'cacheCreationTokens', 'cacheReadTokens'))
            tok_mm[b['modelName']][mm['period']] += tk
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
    for s in session_raw.get('session', []):
        cost_by_uuid[s['period']] = s['totalCost']
        tok_by_uuid[s['period']]  = s['totalTokens']
    agg = defaultdict(lambda: [0.0, 0, 0])   # cost, tok, sessions
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
    projects = sorted(
        [{"n": n, "s": v[2], "t": v[1], "c": round(v[0], 2)} for n, v in agg.items() if v[0] >= 0.01],
        key=lambda x: -x['c'])
    if not projects:   # session->dir mapping unavailable; degrade gracefully
        projects = [{"n": "(all sessions)", "s": len(cost_by_uuid),
                     "t": total_tokens, "c": round(total_cost, 2)}]

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
        "tokKind": tokKind,
        "defaultPrices": default_prices,
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
