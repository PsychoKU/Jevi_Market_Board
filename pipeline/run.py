"""Orchestrates one run. Called every ~15 minutes by GitHub Actions.

quick  : a market whose session is open (plus 20 min before / 25 min after) -> refresh quotes, index charts, flows,
         disclosures, news, options / KIS derivatives.
full   : once per trading day after the close -> also universe + sectors, 2-year daily history, 52-week trend,
         valuations, local names, analyst estimates.
Usage  : python pipeline/run.py [--force kr,jp,us] [--full]
"""
import sys, time, argparse, traceback, datetime as dt
from common import load, save, log, in_window, after_close, market_date, local_now, r2
from yahoo import Yahoo, trend
import korea, japan, us, news, build

BUDGET = 40 * 60          # seconds per run (workflow timeout is 55 min)
T0 = time.time()
def left(): return BUDGET - (time.time() - T0)

INDEX_CH = {'jp': ['^N225'], 'kr': ['^KS11', '^KQ11'], 'us': ['^GSPC', '^IXIC']}
VIXH = ['^VIX9D', '^VIX', '^VIX3M', '^VIX6M', '^SKEW']
EXTRA_Q = ['^VIX9D', '^VIX3M', '^VIX6M', '^VVIX', '^SKEW', '^VXN', '^OVX', '^MOVE']
BENCH = {'jp': '1306.T', 'kr': '^KS11', 'us': '^GSPC'}
LANG = {'kr': ('ko-KR', 'KR'), 'jp': ('ja-JP', 'JP')}

def ext_from(q):
    dy = q.get('dividendYield') if q.get('dividendYield') is not None else ((q.get('trailingAnnualDividendYield') or 0) * 100 or None)
    return {'pe': q.get('trailingPE'), 'pb': q.get('priceToBook'), 'dy': dy, 'fpe': q.get('forwardPE')}

# ---------------- shared market data (indices, fx, rates, VIX) ----------------
def refresh_market(y, ms):
    mk = load('market.json', {})
    syms = sorted(set(build.MACRO_SYMS) | set(EXTRA_Q) | set(BENCH.values()))
    q = y.quotes(syms)
    if q: mk['q'] = {**mk.get('q', {}), **q}
    ch = mk.setdefault('ch', {})
    for m in ms:
        for s in INDEX_CH[m]:
            d = y.chart(s, '1d', '5m'); yy = y.chart(s, '1y', '1d')
            if d or yy: ch[s] = {'d': d or (ch.get(s) or {}).get('d'), 'y': yy or (ch.get(s) or {}).get('y')}
    # benchmark returns
    sp = y.spark(list(BENCH.values()), '1y'); bench = mk.setdefault('bench', {})
    for s, h in sp.items():
        c = [v for v in h['c'] if v is not None]
        if len(c) > 61: bench[s] = {k: r2((c[-1] / c[-1 - n] - 1) * 100, 2) for k, n in (('r1w', 5), ('r4w', 20), ('r12w', 60))}
    if 'us' in ms or not mk.get('vixh'):
        vh = {}
        for s in VIXH:
            c = y.chart(s, '1y', '1d')
            if c: vh[s] = c['pts']
        if vh: mk['vixh'] = vh
    mk['at'] = int(time.time())
    save('market.json', mk)

# ---------------- per-market steps ----------------
def universe_full(y, m):
    raw = y.universe(m, with_sectors=(m == 'us' or m == 'kr'))
    if not raw: return False
    save(f'{m}_raw.json', {s: v['q'] for s, v in raw.items()})
    save(f'{m}_sector.json', {s: v['sec'] for s, v in raw.items() if v['sec']})
    save(f'{m}_ext.json', {s: ext_from(v['q']) for s, v in raw.items()})
    syms = list(raw)
    if m in LANG:
        lang, reg = LANG[m]; loc = y.quotes(syms, fields=['shortName', 'longName'], lang=lang, region=reg)
        prev = load(f'{m}_loc.json', {})
        prev.update({s: (v.get('longName') or v.get('shortName')) for s, v in loc.items() if v.get('longName') or v.get('shortName')})
        save(f'{m}_loc.json', prev)
    hist = y.spark(syms, '2y')
    if len(hist) > len(syms) * 0.5:
        save(f'{m}_hist.json', hist)
        save(f'{m}_trend.json', trend(hist, m, len(syms)))
    return True

def quotes_quick(y, m):
    raw = load(f'{m}_raw.json', {})
    if not raw: return universe_full(y, m)
    q = y.quotes(list(raw))
    if len(q) < len(raw) * 0.5: log.warning('quick quotes %s only %d/%d', m, len(q), len(raw)); return False
    for s, v in q.items(): raw[s] = {**raw[s], **v}
    save(f'{m}_raw.json', raw)
    ext = load(f'{m}_ext.json', {})
    for s, v in q.items():
        e = ext_from(v)
        if any(x is not None for x in e.values()): ext[s] = e
    save(f'{m}_ext.json', ext)
    return True

def estimates_step(y, m, max_syms=None):
    """Analyst EPS revisions (Yahoo epsTrend). Resumable: continues where the previous run stopped."""
    raw = load(f'{m}_raw.json', {})
    if not raw: return
    U = sorted(([s, q.get('marketCap'), q.get('regularMarketPrice')] for s, q in raw.items() if q.get('marketCap')), key=lambda x: -(x[1] or 0))
    if m == 'us': U = [u for u in U if (u[1] or 0) >= 3e8]
    prog = load(f'{m}_est_prog.json', {})
    today = market_date(m)
    if prog.get('date') != today: prog = {'date': today, 'done': [], 'est': {}}
    todo = [u[0] for u in U if u[0] not in set(prog['done'])]
    if max_syms: todo = todo[:max_syms]
    step = 200
    for i in range(0, len(todo), step):
        if left() < 6 * 60: break
        b = todo[i:i + step]; got = y.estimates(b, workers=3, pause=0.2)
        prog['est'].update(got); prog['done'] += b
        save(f'{m}_est_prog.json', prog)
        if len(got) < len(b) * 0.3: log.warning('estimates %s throttled; resume next run', m); break
    complete = len(set(prog['done'])) >= len(U)
    if complete or not load(f'{m}_est.json'):
        save(f'{m}_est.json', {'asof': today + ('' if complete else ' (일부)'), 'universe': U, 'est': prog['est']})
    log.info('estimates %s %d/%d', m, len(set(prog['done'])), len(U))

def kr_steps(full, open_now):
    s = korea.daum_session()
    names, krx = korea.daum_lists(s)
    if names: save('kr_names.json', names)
    inv = korea.daum_investors(s)
    if inv.get('KOSPI'): save('kr_investors.json', inv)
    if full:
        codes = list(names or load('kr_names.json', {}))
        det = korea.daum_details(s, codes)
        if det: save('kr_daum.json', {**load('kr_daum.json', {}), **det})
    today = local_now('kr').date().isoformat()
    ks = korea.session()
    try:
        rows, big = korea.kind_today_and_holdings(ks, market_date('kr'))
        save('kr_kind.json', {'date': market_date('kr'), 'today': rows, 'big': big})
    except Exception: log.warning('KIND failed\n%s', traceback.format_exc())
    api = korea.KIS_API()
    if api.ok and (open_now or full):
        snap = korea.kis_snapshot(api)
        if snap:
            kt = load('kis_today.json', {})
            if kt.get('date') != today: kt = {'date': today, 'snaps': []}
            kt['snaps'].append(snap); kt['snaps'] = kt['snaps'][-60:]
            save('kis_today.json', kt)
            log.info('KIS flows %s; fields %s', list(snap['flows']), snap['raw_keys'][:20])

def jp_steps(full):
    s = japan.session()
    if full or not load('jpx_list.json'): japan.listing(s)
    if full or not load('jp_flows.json'): japan.weekly_flows(s)
    japan.jgb(s)
    ymd = market_date('jp').replace('-', '')
    rows = japan.tdnet(s, ymd)
    if rows or load('jp_tdnet.json', {}).get('date') != market_date('jp'):
        save('jp_tdnet.json', {'date': market_date('jp'), 'rows': rows})

def us_steps(y):
    s = us.sec_session()
    if s is None: log.warning('SEC_UA secret missing: skipping 8-K/13D')
    else:
        try: us.filings(s)
        except Exception: log.warning('SEC failed\n%s', traceback.format_exc())
    O = load('us_options.json', {})
    for sym in ('SPY', 'QQQ', 'IWM', 'NVDA', 'TSLA'):
        o = y.options(sym)
        if o: O[sym] = o
    save('us_options.json', O)

def run_market(y, m, full, open_now):
    log.info('=== %s %s', m, 'full' if full else 'quick')
    ok = universe_full(y, m) if full else quotes_quick(y, m)
    for step in ((lambda: kr_steps(full, open_now)) if m == 'kr' else (lambda: jp_steps(full)) if m == 'jp' else (lambda: us_steps(y)),
                 lambda: save(f'{m}_news.json', news.headlines(m) or load(f'{m}_news.json', []))):
        try: step()
        except Exception: log.warning('%s step failed\n%s', m, traceback.format_exc())
    if full and left() > 8 * 60:
        try: estimates_step(y, m)
        except Exception: log.warning('estimates failed\n%s', traceback.format_exc())
    return ok

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--force', default=''); ap.add_argument('--full', action='store_true')
    a = ap.parse_args()
    done = load('done.json', {})
    plan = []
    for m in ('us', 'kr', 'jp'):
        forced = m in a.force.split(',') or a.force == 'all'
        open_now = in_window(m)
        need_full = after_close(m) and done.get(m) != market_date(m)
        if forced: plan.append((m, a.full or need_full or not load(f'{m}_raw.json'), open_now))
        elif need_full: plan.append((m, True, False))
        elif open_now: plan.append((m, False, True))
        elif not load(f'{m}.json', base=build.OUT): plan.append((m, True, False))
    # also keep an unfinished estimate collection going outside market hours
    pending = [m for m in ('us', 'kr', 'jp') if load(f'{m}_est_prog.json', {}).get('date') == market_date(m)
               and not in_window(m) and m not in [p[0] for p in plan] and not (load(f'{m}_est.json') or {}).get('asof', '').startswith(market_date(m) + '')]
    log.info('plan %s pending-est %s', plan, pending)
    if not plan and not pending:
        log.info('nothing to do')
        if not a.force: return
    y = Yahoo()
    try: refresh_market(y, [p[0] for p in plan] or ['us'])
    except Exception: log.warning('market refresh failed\n%s', traceback.format_exc())
    for m, full, open_now in plan:
        ok = False
        try: ok = run_market(y, m, full, open_now)
        except Exception: log.error('%s failed\n%s', m, traceback.format_exc())
        if full and ok: done[m] = market_date(m); save('done.json', done)
    for m in pending:
        if left() > 8 * 60:
            try: estimates_step(y, m)
            except Exception: log.warning('estimates failed\n%s', traceback.format_exc())
    for m in ('us', 'kr', 'jp'):
        if not load(f'{m}_raw.json'): continue
        try:
            D = build.build(m, in_window(m))
            if D and (m in [p[0] for p in plan if p[1]] or not load(f'{m}.json', base=build.DIGEST)): build.digest(m, D)
        except Exception: log.error('build %s failed\n%s', m, traceback.format_exc())
    import os
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as f: f.write('built=1\n')
    log.info('run finished in %.0fs', time.time() - T0)

if __name__ == '__main__':
    main()
