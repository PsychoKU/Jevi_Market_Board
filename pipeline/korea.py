"""Korea: Daum Finance (names, valuations, sectors, investor flows), KRX KIND (disclosures, 5% reports),
Korea Investment & Securities Open API (futures/options investor flows, program trading, option board)."""
import os, re, time, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from common import session, fetch, jget, log, load, save, r2, local_now

DAUM = 'https://finance.daum.net'

def daum_session():
    s = session({'Referer': 'https://finance.daum.net/domestic', 'Accept': 'application/json, text/plain, */*'})
    fetch(s, 'GET', DAUM + '/domestic', ok=(200,))
    return s

def daum_lists(s):
    """Short names and KRX sector per stock code, from the sector pages (2 requests)."""
    names, krx = {}, {}
    for m in ('KOSPI', 'KOSDAQ'):
        j = jget(s, f'{DAUM}/api/quotes/sectors?fieldName=&order=&perPage=&market={m}&changes=UPPER_LIMIT%2CRISE%2CEVEN%2CFALL%2CLOWER_LIMIT')
        for sec in (j or {}).get('data', []):
            for st in sec.get('includedStocks', []):
                c = st['symbolCode'][1:]; names[c] = st['name']; krx[c] = sec['sectorName']
    log.info('daum names %d', len(names))
    return names, krx

def daum_details(s, codes, workers=8):
    """PER/PBR/DPS/WICS sector/foreign ownership/summary per stock (one request each; run once a day)."""
    def job(c):
        j = jget(s, f'{DAUM}/api/quotes/A{c}?summary=false&changeStatistics=true', tries=3)
        if not j: return c, None
        fr = j.get('foreignRatio')
        return c, [j.get('per'), j.get('pbr'), j.get('dps'), j.get('wicsSectorName'), None,
                   round(fr * 100, 1) if fr is not None else None, re.sub(r'\s+', ' ', j.get('companySummary') or '')[:160]]
    out = {}
    with ThreadPoolExecutor(workers) as ex:
        for c, v in ex.map(job, codes):
            if v: out[c] = v
    log.info('daum details %d/%d', len(out), len(codes))
    return out

def daum_investors(s):
    out = {}
    for m in ('KOSPI', 'KOSDAQ'):
        j = jget(s, f'{DAUM}/api/investor/{m}/days?perPage=60&details=true')
        out[m] = (j or {}).get('data') or []
    return out

# ---------------- KIND ----------------
KIND = 'https://kind.krx.co.kr'

def kind_day(s, d):
    rows = []
    for p in range(1, 16):
        r = fetch(s, 'POST', KIND + '/disclosure/todaydisclosure.do', data={
            'method': 'searchTodayDisclosureSub', 'currentPageSize': '100', 'pageIndex': str(p), 'orderMode': '0', 'orderStat': 'D',
            'forward': 'todaydisclosure_sub', 'chose': 'S', 'todayFlag': 'N', 'selDate': d})
        if r is None: break
        soup = BeautifulSoup(r.text, 'html.parser'); n = 0
        for tr in soup.select('table tbody tr'):
            td = tr.find_all('td')
            if len(td) < 4: continue
            n += 1; a = td[2].find('a'); comp = td[1].find('a'); img = td[1].find('img')
            rid = re.search(r"'(\d{14})'", (a.get('onclick') or '') if a else '')
            rows.append([f'{d} {td[0].get_text(strip=True)}', img.get('alt', '') if img else '', (comp or td[1]).get_text(strip=True),
                         re.sub(r'\s+', ' ', (a or td[2]).get_text(' ', strip=True)), rid.group(1) if rid else '', td[3].get_text(strip=True)])
        if n < 100: break
        time.sleep(0.2)
    return rows

def kind_today_and_holdings(s, today, days_back=10):
    today_rows = kind_day(s, today)
    big = [r for r in today_rows if '대량보유' in r[3]]
    d = dt.date.fromisoformat(today); got = 0
    cache = load('kr_kind_big.json', {})
    while got < days_back - 1:
        d -= dt.timedelta(days=1)
        if d.weekday() >= 5: continue
        k = d.isoformat()
        if k not in cache: cache[k] = [r for r in kind_day(s, k) if '대량보유' in r[3]]
        big += cache[k]; got += 1
    keep = sorted(cache)[-15:]; save('kr_kind_big.json', {k: cache[k] for k in keep})
    return today_rows, big

# ---------------- KIS Open API ----------------
KIS = 'https://openapi.koreainvestment.com:9443'

class KIS_API:
    """Read-only quotations. Keys come from repository secrets KIS_APP_KEY / KIS_APP_SECRET."""
    def __init__(self):
        self.key, self.secret = os.environ.get('KIS_APP_KEY'), os.environ.get('KIS_APP_SECRET')
        self.ok = bool(self.key and self.secret)
        self.s = session({'content-type': 'application/json; charset=utf-8'})
        self.token = None
        if self.ok: self._token()

    def _token(self):
        cached = load('kis_token.json', {})
        if cached.get('token') and cached.get('exp', 0) > time.time() + 600:
            self.token = cached['token']; return
        r = fetch(self.s, 'POST', KIS + '/oauth2/tokenP', json={'grant_type': 'client_credentials', 'appkey': self.key, 'appsecret': self.secret}, tries=2)
        if r is None:
            log.warning('KIS token failed'); self.ok = False; return
        j = r.json(); self.token = j.get('access_token')
        if not self.token: log.warning('KIS token missing: %s', str(j)[:200]); self.ok = False; return
        save('kis_token.json', {'token': self.token, 'exp': time.time() + int(j.get('expires_in', 86400))})

    def get(self, path, tr_id, params):
        if not self.ok: return None
        h = {'authorization': f'Bearer {self.token}', 'appkey': self.key, 'appsecret': self.secret, 'tr_id': tr_id, 'custtype': 'P'}
        r = fetch(self.s, 'GET', KIS + path, headers=h, params=params, tries=3)
        time.sleep(0.12)  # stay under the per-second limit
        if r is None: return None
        j = r.json()
        if j.get('rt_cd') not in ('0', 0, None):
            log.warning('KIS %s %s: %s', tr_id, params, j.get('msg1')); return None
        return j

    def investor_by_market(self, iscd, iscd2):
        j = self.get('/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market', 'FHPTJ04030000',
                     {'FID_INPUT_ISCD': iscd, 'FID_INPUT_ISCD_2': iscd2})
        o = (j or {}).get('output')
        return (o[0] if isinstance(o, list) and o else o) or None

    def program_today(self, mkt):
        j = self.get('/uapi/domestic-stock/v1/quotations/investor-program-trade-today', 'HHPPG046600C1', {'MRKT_DIV_CLS_CODE': mkt, 'EXCH_DIV_CLS_CODE': 'J'})
        return (j or {}).get('output1')

    def futures_board(self):
        for cls in ('', 'K2I'):
            j = self.get('/uapi/domestic-futureoption/v1/quotations/display-board-futures', 'FHPIF05030200',
                         {'FID_COND_MRKT_DIV_CODE': 'F', 'FID_COND_SCR_DIV_CODE': '20503', 'FID_COND_MRKT_CLS_CODE': cls})
            if j and j.get('output'): return j['output']
        return None

    def option_months(self):
        j = self.get('/uapi/domestic-futureoption/v1/quotations/display-board-option-list', 'FHPIO056104C0',
                     {'FID_COND_SCR_DIV_CODE': '509', 'FID_COND_MRKT_DIV_CODE': '', 'FID_COND_MRKT_CLS_CODE': ''})
        return [x.get('mtrt_yymm') for x in ((j or {}).get('output') or []) if x.get('mtrt_yymm')]

    def option_board(self, yymm):
        j = self.get('/uapi/domestic-futureoption/v1/quotations/display-board-callput', 'FHPIF05030100',
                     {'FID_COND_MRKT_DIV_CODE': 'O', 'FID_COND_SCR_DIV_CODE': '20503', 'FID_MRKT_CLS_CODE': 'CO',
                      'FID_MTRT_CNT': yymm, 'FID_MRKT_CLS_CODE1': 'PO', 'FID_COND_MRKT_CLS_CODE': ''})
        if not j: return None
        return {'calls': j.get('output1') or [], 'puts': j.get('output2') or []}

    def option_price(self, code):
        j = self.get('/uapi/domestic-futureoption/v1/quotations/inquire-price', 'FHMIF10000000',
                     {'FID_COND_MRKT_DIV_CODE': 'O', 'FID_INPUT_ISCD': code})
        return (j or {}).get('output1') or None

# Option short codes look like B01612C41: B/C = call/put, 01 = KOSPI200, 612 = 2026-12, then a 3-char serial that
# moves by one per listed strike (C41 -> C40 -> ... -> C00 -> B99). The board only returns the 100 highest strikes,
# so the near-the-money chain is reached by stepping that serial down from the board and reading each strike.
_D36 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
def _sidx(code): return _D36.index(code[-3]) * 100 + int(code[-2:])
def _scode(prefix, i): return prefix + _D36[i // 100] + f'{i % 100:02d}'

def option_chain_near(api, board_calls, spot, width=0.07, max_calls=110):
    """Strike -> row for calls and puts within +-width of spot, read one option at a time."""
    rows = [(x.get('optn_shrn_iscd'), _num(x.get('acpr'))) for x in board_calls if x.get('optn_shrn_iscd') and _num(x.get('acpr'))]
    if len(rows) < 2 or not spot: return None
    rows.sort(key=lambda r: _sidx(r[0]))
    (c0, k0), (c1, k1) = rows[0], rows[1]
    step = (k1 - k0) / (_sidx(c1) - _sidx(c0)) or 2.5
    prefix = c0[:-3]; base = _sidx(c0)
    guess = base - int(round((k0 - spot) / step)); used = 0
    def read(i, kind):
        nonlocal used
        used += 1
        return api.option_price(('B' if kind == 'c' else 'C') + _scode(prefix, i)[1:])
    # correct the guess once using the real strike at the guessed code
    o = read(guess, 'c'); k = _num((o or {}).get('acpr'))
    for probe in (guess, base - 1, base - 3):
        oo = api.option_price('B' + _scode(prefix, probe)[1:])
        log.info('KIS probe %s -> %s', _scode(prefix, probe), {kk: (oo or {}).get(kk) for kk in ('hts_kor_isnm', 'acpr', 'futs_prpr', 'hts_otst_stpl_qty')} if oo else None)
    if k: guess += int(round((spot - k) / 2.5))
    lo, hi = spot * (1 - width), spot * (1 + width)
    out = {'calls': [], 'puts': []}
    span = int(spot * width / 2.5) + 2
    for i in range(guess - span, guess + span + 1):
        if used >= max_calls: break
        for kind in ('c', 'p'):
            o = read(i, kind)
            if not o: continue
            k = _num(o.get('acpr'))
            if not k or not (lo <= k <= hi): continue
            out['calls' if kind == 'c' else 'puts'].append([k, _num(o.get('hts_otst_stpl_qty')), _num(o.get('otst_stpl_qty_icdc')), _num(o.get('acml_vol')),
                                                            _num(o.get('hts_ints_vltl')), _num(o.get('gama')), _num(o.get('futs_prpr')), _num(o.get('delta_val'))])
    log.info('KIS near-the-money chain: %d calls, %d puts, %d requests', len(out['calls']), len(out['puts']), used)
    return out if out['calls'] and out['puts'] else None

FLOW_KEYS = [('foreign', 'frgn'), ('indiv', 'prsn'), ('inst', 'orgn'), ('fininv', 'scrt'), ('trust', 'ivtr'), ('pension', 'fund'), ('bank', 'bank'), ('insure', 'insu')]
DERIV = [('fut', 'K2I', 'F001', '코스피200 선물'), ('call', 'K2I', 'OC01', '코스피200 콜'), ('put', 'K2I', 'OP01', '코스피200 풋'),
         ('wcall_thu', 'WKI', 'OC04', '위클리 콜(목)'), ('wput_thu', 'WKI', 'OP04', '위클리 풋(목)'),
         ('wcall_mon', 'WKM', 'OC05', '위클리 콜(월)'), ('wput_mon', 'WKM', 'OP05', '위클리 풋(월)'),
         ('mfut', 'MKI', 'F004', '미니 선물'), ('kq_fut', 'KQI', 'F002', '코스닥150 선물'),
         ('spot_kospi', 'KSP', '0001', '코스피 현물'), ('spot_kosdaq', 'KSQ', '1001', '코스닥 현물')]

def _num(x):
    try: return float(str(x).replace(',', ''))
    except (TypeError, ValueError): return None

def kis_snapshot(api):
    """One intraday snapshot of derivatives/spot investor flows, program trading, futures and option board."""
    if not api.ok: return None
    snap = {'ts': int(time.time()), 'flows': {}, 'program': {}, 'fut': None, 'opt': None, 'raw_keys': {}}
    for key, iscd, iscd2, label in DERIV:
        o = api.investor_by_market(iscd, iscd2)
        if not o: continue
        if not snap['raw_keys']: snap['raw_keys'] = sorted(o.keys())[:80]
        snap['flows'][key] = {'label': label, **{k: {'qty': _num(o.get(f'{p}_ntby_qty')), 'amt': _num(o.get(f'{p}_ntby_tr_pbmn'))} for k, p in FLOW_KEYS}}
    for mk, code in (('kospi', '1'), ('kosdaq', '4')):
        o = api.program_today(code)
        if o: snap['program'][mk] = o if isinstance(o, list) else [o]
    fb = api.futures_board()
    if fb:
        f = fb[0] if isinstance(fb, list) else fb
        snap['fut'] = {'code': f.get('futs_shrn_iscd'), 'name': f.get('hts_kor_isnm'), 'px': _num(f.get('futs_prpr')), 'chg': _num(f.get('futs_prdy_ctrt')),
                       'theo': _num(f.get('hts_thpr')), 'oi': _num(f.get('hts_otst_stpl_qty')), 'vol': _num(f.get('acml_vol')), 'dte': _num(f.get('hts_rmnn_dynu'))}
    months = api.option_months()
    if months:
        b = api.option_board(months[0])
        if b:
            row = lambda x: [_num(x.get('acpr')), _num(x.get('hts_otst_stpl_qty')), _num(x.get('otst_stpl_qty_icdc')), _num(x.get('acml_vol')),
                             _num(x.get('hts_ints_vltl')), _num(x.get('gama')), _num(x.get('optn_prpr')), _num(x.get('delta_val'))]
            snap['opt'] = {'month': months[0], 'calls': [row(x) for x in b['calls']], 'puts': [row(x) for x in b['puts']]}
            spot = (snap.get('fut') or {}).get('px')
            ks = [r[0] for r in snap['opt']['calls'] if r[0]]
            if spot and ks and min(ks) > spot * 0.97:      # board missed the money
                try:
                    near = option_chain_near(api, b['calls'], spot)
                    if near: snap['opt'].update(near)
                except Exception as e: log.warning('near chain failed: %s', e)
    return snap
