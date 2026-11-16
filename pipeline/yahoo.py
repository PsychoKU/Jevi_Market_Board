"""Yahoo Finance: universe screener, quotes, daily history, index charts, analyst estimates, option chains."""
import time, datetime as dt
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from concurrent.futures import ThreadPoolExecutor
from common import session, fetch, jget, log, r2

Q2 = 'https://query2.finance.yahoo.com'
SECTORS = ['Technology', 'Financial Services', 'Healthcare', 'Consumer Cyclical', 'Industrials', 'Communication Services',
           'Consumer Defensive', 'Energy', 'Basic Materials', 'Real Estate', 'Utilities']
US_EX = {'operator': 'or', 'operands': [{'operator': 'eq', 'operands': ['exchange', e]} for e in ['NMS', 'NYQ', 'NGM', 'NCM', 'ASE', 'PCX', 'BTS']]}
REGION = {'kr': 'kr', 'jp': 'jp', 'us': 'us'}

class Yahoo:
    def __init__(self):
        self.s = session({'Accept': 'application/json,text/plain,*/*', 'Origin': 'https://finance.yahoo.com', 'Referer': 'https://finance.yahoo.com/'})
        fetch(self.s, 'GET', 'https://fc.yahoo.com', ok=(200, 404))          # sets the A3 cookie
        fetch(self.s, 'GET', 'https://finance.yahoo.com/quote/AAPL/', ok=(200,))
        r = fetch(self.s, 'GET', Q2 + '/v1/test/getcrumb')
        self.crumb = r.text.strip() if r is not None else ''
        if not self.crumb or len(self.crumb) > 30: raise RuntimeError('Yahoo crumb unavailable')
        log.info('yahoo crumb ok')

    def _p(self, extra=''):
        return f'crumb={self.crumb}' + extra

    # ---------- screener ----------
    def screen(self, ops, offset):
        body = {'offset': offset, 'size': 250, 'sortField': 'intradaymarketcap', 'sortType': 'DESC', 'quoteType': 'EQUITY',
                'query': {'operator': 'and', 'operands': ops}, 'userId': '', 'userIdType': 'guid'}
        r = fetch(self.s, 'POST', f'{Q2}/v1/finance/screener?{self._p()}&lang=en-US&region=US&formatted=false', json=body)
        try: return r.json()['finance']['result'][0] if r is not None else {'total': 0, 'quotes': []}
        except (KeyError, IndexError, ValueError): return {'total': 0, 'quotes': []}

    def _pages(self, ops):
        out, off, total = [], 0, 1
        while off < total and off < 9750:
            res = self.screen(ops, off); total = res.get('total', 0); qs = res.get('quotes') or []
            if not qs: break
            out += qs; off += 250
        return out

    def universe(self, m, with_sectors):
        base = [{'operator': 'eq', 'operands': ['region', REGION[m]]}] + ([US_EX] if m == 'us' else [])
        raw = {}
        if with_sectors:
            jobs = [(s, base + [{'operator': 'eq', 'operands': ['sector', s]}]) for s in SECTORS] + [(None, base)]
            with ThreadPoolExecutor(6) as ex:
                for s, qs in zip([j[0] for j in jobs], ex.map(lambda j: self._pages(j[1]), jobs)):
                    for q in qs:
                        if q['symbol'] not in raw: raw[q['symbol']] = {'q': q, 'sec': s}
                        elif s and not raw[q['symbol']]['sec']: raw[q['symbol']]['sec'] = s
        else:
            for q in self._pages(base): raw[q['symbol']] = {'q': q, 'sec': None}
        log.info('universe %s %d', m, len(raw))
        return raw

    # ---------- quotes ----------
    def quotes(self, syms, fields=None, lang=None, region=None, chunk=150):
        out = {}
        def job(b):
            u = f"{Q2}/v7/finance/quote?symbols={','.join(b)}&{self._p()}"
            if fields: u += '&fields=' + ','.join(fields)
            if lang: u += f'&lang={lang}&region={region}'
            j = jget(self.s, u)
            return (j or {}).get('quoteResponse', {}).get('result', [])
        batches = [syms[i:i + chunk] for i in range(0, len(syms), chunk)]
        with ThreadPoolExecutor(6) as ex:
            for res in ex.map(job, batches):
                for x in res: out[x['symbol']] = x
        return out

    def spark(self, syms, rng='2y'):
        out = {}
        def job(b):
            j = jget(self.s, f"{Q2}/v7/finance/spark?symbols={','.join(b)}&range={rng}&interval=1d&{self._p()}")
            res = {}
            for x in (j or {}).get('spark', {}).get('result', []) or []:
                r0 = (x.get('response') or [None])[0]
                if r0 and r0.get('timestamp'):
                    res[x['symbol']] = {'t': r0['timestamp'], 'c': r0['indicators']['quote'][0]['close']}
            return res
        batches = [syms[i:i + 20] for i in range(0, len(syms), 20)]
        with ThreadPoolExecutor(8) as ex:
            for res in ex.map(job, batches): out.update(res)
        log.info('spark %d/%d', len(out), len(syms))
        return out

    def chart(self, sym, rng, interval):
        j = jget(self.s, f'{Q2}/v8/finance/chart/{requests_quote(sym)}?range={rng}&interval={interval}&{self._p()}')
        try:
            r0 = j['chart']['result'][0]; c = r0['indicators']['quote'][0]['close']
            pts = [[t, round(v, 2)] for t, v in zip(r0['timestamp'], c) if v is not None]
            return {'prev': r0['meta'].get('chartPreviousClose') or r0['meta'].get('previousClose'), 'pts': pts}
        except (TypeError, KeyError, IndexError): return None

    # ---------- analyst estimates ----------
    def estimates(self, syms, workers=4, pause=0.15):
        out, fails = {}, 0
        def pick(t, p):
            x = next((z for z in t if z.get('period') == p), None)
            if not x: return None
            e = x.get('epsTrend') or {}; rv = x.get('epsRevisions') or {}
            v = lambda d, k: (d.get(k) or {}).get('raw')
            return [x.get('endDate'), v(e, 'current'), v(e, '7daysAgo'), v(e, '30daysAgo'), v(e, '90daysAgo'),
                    v(x.get('earningsEstimate') or {}, 'numberOfAnalysts'), v(rv, 'upLast7days'),
                    v(rv, 'downLast7Days') if rv.get('downLast7Days') else v(rv, 'downLast7days'),
                    v(rv, 'upLast30days'), v(rv, 'downLast30days'), v(x.get('revenueEstimate') or {}, 'avg')]
        def job(sym):
            time.sleep(pause)
            j = jget(self.s, f'{Q2}/v10/finance/quoteSummary/{requests_quote(sym)}?modules=earningsTrend,financialData&{self._p()}', tries=3)
            try: res = j['quoteSummary']['result'][0]
            except (TypeError, KeyError, IndexError): return sym, None
            t = (res.get('earningsTrend') or {}).get('trend') or []; fd = res.get('financialData') or {}
            g = lambda k: (fd.get(k) or {}).get('raw')
            return sym, {'y0': pick(t, '0y'), 'y1': pick(t, '+1y'), 'q0': pick(t, '0q'), 'tp': g('targetMeanPrice'),
                         'rec': g('recommendationMean'), 'na': g('numberOfAnalystOpinions')}
        with ThreadPoolExecutor(workers) as ex:
            for sym, v in ex.map(job, syms):
                if v: out[sym] = v
                else: fails += 1
        log.info('estimates %d ok, %d failed', len(out), fails)
        return out

    # ---------- options ----------
    def options(self, sym, days=50, max_exp=14):
        first = jget(self.s, f'{Q2}/v7/finance/options/{requests_quote(sym)}?{self._p()}')
        try: res = first['optionChain']['result'][0]
        except (TypeError, KeyError, IndexError): return None
        now = time.time(); exps = [d for d in res['expirationDates'] if d < now + days * 86400][:max_exp]
        q = res['quote']; out = {'px': q.get('regularMarketPrice'), 'ts': q.get('regularMarketTime'), 'exps': []}
        m = lambda x: [x['strike'], x.get('openInterest') or 0, x.get('volume') or 0, round(x.get('impliedVolatility') or 0, 4), x.get('lastPrice')]
        for d in exps:
            c = res if d == res['expirationDates'][0] else None
            if c is None:
                j = jget(self.s, f'{Q2}/v7/finance/options/{requests_quote(sym)}?date={d}&{self._p()}')
                try: c = j['optionChain']['result'][0]
                except (TypeError, KeyError, IndexError): continue
            o = (c.get('options') or [{}])[0]
            out['exps'].append({'d': d, 'calls': [m(x) for x in o.get('calls', [])], 'puts': [m(x) for x in o.get('puts', [])]})
        return out if out['exps'] else None

def requests_quote(sym):
    from urllib.parse import quote
    return quote(sym, safe='')

# ---------- row building (port of the browser version) ----------
SEG = {'kr': {'KSC': 'KOSPI', 'KOE': 'KOSDAQ'},
       'us': {'NMS': 'Nasdaq', 'NGM': 'Nasdaq', 'NCM': 'Nasdaq', 'NYQ': 'NYSE', 'ASE': 'NYSE American', 'PCX': 'NYSE Arca', 'BTS': 'Cboe'}}
UNIT = {'jp': 1e8, 'kr': 1e8, 'us': 1e6}
COLS = ['code', 'nm', 'en', 'sec', 'seg', 'px', 'chg', 'mcap', 'to', 'fh', 'fl', 'c52', 'pe', 'pb', 'dy', 'nh', 'nl', 'a200', 'a50', 'vr',
        'r1w', 'r4w', 'r12w', 'ytd', 'nhd', 'hi', 'lo', 'fpe', 'ts']

def closes_for(hist, mdate, px):
    """Daily closes with the current price as the last point (drops a stored close for the same trading date)."""
    if not hist: return [px] if px else []
    t, c = hist['t'], hist['c']
    pts = [(tt, v) for tt, v in zip(t, c) if v is not None]
    if pts and dt.datetime.utcfromtimestamp(pts[-1][0]).date().isoformat() >= mdate: pts = pts[:-1]
    return [v for _, v in pts] + ([px] if px else [])

def build_row(m, sym, q, sec, ext, hist, loc, mdate):
    px = q.get('regularMarketPrice')
    if not px: return None
    c = closes_for(hist, mdate, px); last = c[-1]
    ret = lambda n: r2((last / c[-1 - n] - 1) * 100, 1) if len(c) > n and c[-1 - n] else None
    ytd = None
    if hist:
        for tt, v in zip(reversed(hist['t']), reversed(hist['c'])):
            if v is not None and dt.datetime.utcfromtimestamp(tt).year <= dt.date.fromisoformat(mdate).year - 1:
                ytd = r2((last / v - 1) * 100, 1); break
    nhd = None
    if len(c) > 220:
        a = np.asarray(c[-271:], dtype=float)
        for k in range(20):
            i = len(a) - 1 - k; w = a[max(0, i - 250):i]
            if w.size and a[i] >= w.max(): nhd = k; break
    U = UNIT[m]; hi, lo = q.get('fiftyTwoWeekHigh'), q.get('fiftyTwoWeekLow')
    vol, av = q.get('regularMarketVolume') or 0, q.get('averageDailyVolume3Month') or 0
    e = ext or {}
    code = sym if m == 'us' else sym.rsplit('.', 1)[0]
    nm = (loc or q.get('longName') or q.get('shortName') or '').replace('(주)', '').replace('㈜', '').strip()
    en = ((q.get('longName') or q.get('shortName')) if m == 'us' else (q.get('shortName') or q.get('longName'))) or sym
    a200 = q.get('twoHundredDayAverageChangePercent'); a50 = q.get('fiftyDayAverageChangePercent')
    return [code, nm, en.strip(), sec or '', SEG.get(m, {}).get(q.get('exchange'), q.get('exchange') or '') if m != 'jp' else '',
            r2(px, 0 if m == 'kr' else 2), r2(q.get('regularMarketChangePercent'), 2), r2(q['marketCap'] / U, 0) if q.get('marketCap') else None,
            r2(px * vol / U, 1), r2((px / hi - 1) * 100, 1) if hi else None, r2((px / lo - 1) * 100, 1) if lo else None,
            r2(q.get('fiftyTwoWeekChangePercent'), 1), r2(e.get('pe'), 1), r2(e.get('pb'), 2), r2(e.get('dy'), 2),
            1 if (q.get('regularMarketDayHigh') and hi and q['regularMarketDayHigh'] >= hi) else 0,
            1 if (q.get('regularMarketDayLow') and lo and q['regularMarketDayLow'] <= lo) else 0,
            r2(a200 * 100, 1) if a200 is not None else None, r2(a50 * 100, 1) if a50 is not None else None,
            r2(vol / av, 2) if av else None, ret(5), ret(20), ret(60), ytd, nhd,
            r2(hi, 0 if m == 'kr' else 2), r2(lo, 0 if m == 'kr' else 2), r2(e.get('fpe'), 1), q.get('regularMarketTime')]

def trend(hist_by_sym, m, n_syms):
    """52-week closing highs/lows and share above the 200-day average, per day, for the last year."""
    off = {'kr': 9, 'jp': 9, 'us': -5}[m]; acc = {}
    for h in hist_by_sym.values():
        t = np.array([x for x, v in zip(h['t'], h['c']) if v is not None], dtype=np.int64)
        v = np.array([v for v in h['c'] if v is not None], dtype=float); n = len(v)
        if n < 260: continue
        prior = sliding_window_view(v[:-1], 250)            # prior[j] = v[j:j+250] -> compares with v[j+250]
        pmax, pmin = prior.max(axis=1), prior.min(axis=1)    # aligned to index j+250
        cs = np.concatenate([[0.0], np.cumsum(v)])
        ma200 = (cs[200:] - cs[:-200]) / 200                 # ma200[k] = mean(v[k:k+200]) -> index k+199
        start = max(250, n - 260)
        for i in range(start, n):
            d = dt.datetime.utcfromtimestamp(int(t[i]) + off * 3600).date().isoformat()
            a = acc.setdefault(d, [0, 0, 0, 0, 0])
            a[3] += 1; a[4] += 1
            if v[i] >= pmax[i - 250]: a[0] += 1
            if v[i] <= pmin[i - 250]: a[1] += 1
            if v[i] > ma200[i - 199]: a[2] += 1
    ds = [d for d in sorted(acc)[-252:] if acc[d][3] > n_syms * 0.3]
    return {'dates': ds, 'nh': [acc[d][0] for d in ds], 'nl': [acc[d][1] for d in ds],
            'ab': [round(acc[d][2] / acc[d][4] * 100, 1) if acc[d][4] else None for d in ds]}
