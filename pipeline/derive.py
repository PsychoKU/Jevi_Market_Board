"""Derived analytics: option-chain metrics and analyst estimate revisions.
Used both for the artifact snapshot and by the GitHub pipeline."""
import math, datetime as dt, statistics as stx

# ---------------- options ----------------
def _ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def _npdf(x): return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)

def bs_gamma(S, K, T, iv, r=0.04):
    if not (S > 0 and K > 0 and T > 0 and iv and iv > 0.01): return 0.0
    d1 = (math.log(S / K) + (r + iv * iv / 2) * T) / (iv * math.sqrt(T))
    return _npdf(d1) / (S * iv * math.sqrt(T))

def third_friday(y, m):
    d = dt.date(y, m, 1)
    first_fri = d + dt.timedelta(days=(4 - d.weekday()) % 7)
    return first_fri + dt.timedelta(days=14)

def max_pain(calls, puts):
    strikes = sorted({c[0] for c in calls} | {p[0] for p in puts})
    best, bk = None, None
    for k in strikes:
        pay = sum(oi * max(0, k - s) for s, oi, *_ in calls) + sum(oi * max(0, s - k) for s, oi, *_ in puts)
        if best is None or pay < best: best, bk = pay, k
    return bk

def iv_at(rows, target):
    rows = [r for r in rows if r[3] and r[3] > 0.01]
    if not rows: return None
    return min(rows, key=lambda r: abs(r[0] - target))[3]

def option_metrics(sym, o, now_ts):
    S = o['px']; exps = o['exps']
    tot = dict(coi=0, poi=0, cv=0, pv=0); gex = 0.0
    per = []
    for e in exps:
        calls, puts = e['calls'], e['puts']
        T = max((e['d'] + 16 * 3600 - now_ts) / (365 * 86400), 1 / 365 / 4)
        coi = sum(c[1] for c in calls); poi = sum(p[1] for p in puts)
        cv = sum(c[2] for c in calls); pv = sum(p[2] for p in puts)
        tot['coi'] += coi; tot['poi'] += poi; tot['cv'] += cv; tot['pv'] += pv
        g = sum(c[1] * bs_gamma(S, c[0], T, c[3]) for c in calls) - sum(p[1] * bs_gamma(S, p[0], T, p[3]) for p in puts)
        g_dollar = g * 100 * S * S * 0.01  # $ dealer gamma per 1% move, assuming dealers long calls / short puts convention
        gex += g_dollar
        d = dt.datetime.utcfromtimestamp(e['d']).date()
        per.append({'d': d.isoformat(), 'dte': max(0, (d - dt.datetime.utcfromtimestamp(now_ts).date()).days),
                    'coi': coi, 'poi': poi, 'cv': cv, 'pv': pv,
                    'pcr_oi': round(poi / coi, 2) if coi else None, 'mp': max_pain(calls, puts),
                    'atm_iv': iv_at(calls + puts, S), 'gex': round(g_dollar / 1e6, 1)})
    # monthly expiry (3rd Friday) within list
    today = dt.datetime.utcfromtimestamp(now_ts).date()
    tf = third_friday(today.year, today.month)
    if tf < today: tf = third_friday(today.year + (today.month == 12), today.month % 12 + 1)
    mon = next((e for e in exps if dt.datetime.utcfromtimestamp(e['d']).date() == tf), None) or exps[min(len(exps) - 1, 3)]
    near = exps[0]
    lo, hi = S * 0.88, S * 1.12
    def walls(e):
        c = [r for r in e['calls'] if S <= r[0] <= hi]; p = [r for r in e['puts'] if lo <= r[0] <= S]
        return (max(c, key=lambda r: r[1])[0] if c else None, max(p, key=lambda r: r[1])[0] if p else None)
    cw, pw = walls(mon)
    # OI by strike for the monthly expiry, within +-12%
    ks = sorted({r[0] for r in mon['calls'] + mon['puts'] if lo <= r[0] <= hi})
    cmap = {r[0]: r[1] for r in mon['calls']}; pmap = {r[0]: r[1] for r in mon['puts']}
    # thin strikes to at most 45 bars by bucketing
    if len(ks) > 45:
        step = (hi - lo) / 45; buck = {}
        for k in ks:
            b = round(lo + step * int((k - lo) / step), 2)
            x = buck.setdefault(b, [0, 0]); x[0] += cmap.get(k, 0); x[1] += pmap.get(k, 0)
        oi = [[k, v[0], v[1]] for k, v in sorted(buck.items())]
    else:
        oi = [[k, cmap.get(k, 0), pmap.get(k, 0)] for k in ks]
    skew = None
    ivp, ivc = iv_at(mon['puts'], S * 0.95), iv_at(mon['calls'], S * 1.05)
    if ivp and ivc: skew = round((ivp - ivc) * 100, 1)
    return {'sym': sym, 'px': S, 'ts': o.get('ts'),
            'pcr_oi': round(tot['poi'] / tot['coi'], 2) if tot['coi'] else None,
            'pcr_vol': round(tot['pv'] / tot['cv'], 2) if tot['cv'] else None,
            'cv': tot['cv'], 'pv': tot['pv'], 'coi': tot['coi'], 'poi': tot['poi'],
            'gex': round(gex / 1e9, 2),  # $bn per 1% move
            'near': {'d': per[0]['d'], 'mp': per[0]['mp'], 'pcr': per[0]['pcr_oi'], 'iv': per[0]['atm_iv']},
            'mon': {'d': dt.datetime.utcfromtimestamp(mon['d']).date().isoformat(), 'mp': max_pain(mon['calls'], mon['puts']),
                    'cw': cw, 'pw': pw, 'iv': iv_at(mon['calls'] + mon['puts'], S), 'skew': skew, 'oi': oi},
            'exps': per}

# ---------------- estimates ----------------
def _rev(cur, old):
    if cur is None or old is None or old == 0: return None
    r = (cur - old) / abs(old) * 100
    return max(-200.0, min(200.0, r))

def estimate_rows(EST, universe, rows_by_code, code_of):
    """universe: list of [symbol, mcap, price]. returns per-company rows."""
    out = []
    for sym, mcap, price in universe:
        e = EST.get(sym)
        if not e: continue
        code = code_of(sym); base = rows_by_code.get(code)
        def blk(k):
            x = e.get(k)
            if not x or x[1] is None: return None
            end, cur, d7, d30, d90, n, up7, dn7, up30, dn30, rev = x
            return {'end': end, 'eps': cur, 'r1w': _rev(cur, d7), 'r4w': _rev(cur, d30), 'r12w': _rev(cur, d90), 'n': n,
                    'up7': up7, 'dn7': dn7, 'up30': up30, 'dn30': dn30, 'rev': rev, 'old7': d7, 'old30': d30}
        y0, y1 = blk('y0'), blk('y1')
        if not y0 and not y1: continue
        out.append({'sym': sym, 'code': code, 'nm': base['nm'] if base else sym, 'sec': base['sec'] if base else '',
                    'mcap': base['mcap'] if base else None, 'px': price, 'chg1w': base['r1w'] if base else None,
                    'pe': base['pe'] if base else None,
                    'y0': y0, 'y1': y1, 'tp': e.get('tp'), 'rec': e.get('rec'), 'na': e.get('na'),
                    'shares': (mcap / price) if (mcap and price) else None})
    return out

def sector_revisions(comp, key='y1'):
    secs = {}
    for c in comp:
        b = c.get(key)
        if not b or b['r1w'] is None or not c['shares']: continue
        s = secs.setdefault(c['sec'] or '기타', {'n': 0, 'up': 0, 'dn': 0, 'cur': 0.0, 'o7': 0.0, 'o30': 0.0, 'r1': [], 'r4': [], 'mcap': 0})
        s['n'] += 1; s['mcap'] += c['mcap'] or 0
        if b['r1w'] > 0.05: s['up'] += 1
        elif b['r1w'] < -0.05: s['dn'] += 1
        s['r1'].append(b['r1w'])
        if b['r4w'] is not None: s['r4'].append(b['r4w'])
        # aggregate earnings (only positive-earnings names to avoid sign flips)
        # market-cap weighted EPS change (currency-free, so ADRs reporting EPS in local currency are fine)
        if b['eps'] and b['old7'] and b['eps'] > 0 and b['old7'] > 0 and c['mcap']:
            s['cur'] += c['mcap'] * min(2.0, max(0.5, b['eps'] / b['old7'])); s['o7'] += c['mcap']
    out = []
    for k, s in secs.items():
        if s['n'] < 3: continue
        out.append({'sec': k, 'n': s['n'], 'mcap': s['mcap'], 'up': s['up'], 'dn': s['dn'],
                    'breadth': round((s['up'] - s['dn']) / s['n'] * 100, 1),
                    'agg1w': round((s['cur'] / s['o7'] - 1) * 100, 2) if s['o7'] else None,
                    'med1w': round(stx.median(s['r1']), 2), 'med4w': round(stx.median(s['r4']), 2) if s['r4'] else None})
    return sorted(out, key=lambda x: -(x['mcap'] or 0))
