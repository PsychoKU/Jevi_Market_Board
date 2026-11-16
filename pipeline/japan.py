"""Japan: JPX listed-company sheet (segment, 33 sectors, size), JPX weekly investor flows, MOF JGB yields, TDnet."""
import io, re, datetime as dt
from bs4 import BeautifulSoup
from openpyxl import load_workbook
from common import session, fetch, log, load, save

JPX = 'https://www.jpx.co.jp'

def _xlsx_rows(content):
    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    return [list(r) for r in ws.iter_rows(values_only=True)]

def _xls_rows(content):
    import xlrd
    book = xlrd.open_workbook(file_contents=content); sh = book.sheet_by_index(0)
    return [sh.row_values(i) for i in range(sh.nrows)]

def listing(s):
    """Code -> [segment, 33-sector, size class]. The sheet changes monthly; cached for a week."""
    cached = load('jpx_list.json')
    if cached and cached.get('at', '') >= (dt.date.today() - dt.timedelta(days=7)).isoformat(): return cached['m']
    r = fetch(s, 'GET', JPX + '/markets/statistics-equities/misc/01.html')
    if r is None: return (cached or {}).get('m', {})
    href = re.search(r'href="([^"]+data_j\.xlsx?)"', r.text)
    if not href: return (cached or {}).get('m', {})
    f = fetch(s, 'GET', JPX + href.group(1) if href.group(1).startswith('/') else href.group(1))
    rows = (_xlsx_rows if href.group(1).endswith('x') else _xls_rows)(f.content) if f is not None else []
    m = {}
    for r_ in rows[1:]:
        if not r_ or r_[4] in ('-', None): continue
        code = str(r_[1]).replace('.0', '')
        m[code] = [re.sub(r'（.*?）', '', str(r_[3])), r_[5], r_[9]]
    if m: save('jpx_list.json', {'at': dt.date.today().isoformat(), 'm': m})
    log.info('jpx list %d', len(m))
    return m or (cached or {}).get('m', {})

FLOW_N = ['해외투자자', '개인', '신탁은행 (연기금)', '사업법인 (자사주 등)', '투자신탁', '증권 자기매매', '생보·손보', '도시·지방은행']

def weekly_flows(s):
    r = fetch(s, 'GET', JPX + '/markets/statistics-equities/investor-type/index.html')
    if r is None: return load('jp_flows.json', [])
    links = re.findall(r'href="([^"]+stock_1_w_\d+_\d+\.xlsx)"', r.text)[:4]
    out = []
    for u in links:
        f = fetch(s, 'GET', JPX + u if u.startswith('/') else u)
        if f is None: continue
        rows = _xlsx_rows(f.content)
        i = next((k for k, r_ in enumerate(rows) if any('二市場' in str(c) for c in r_ if c)), None)
        if i is None: continue
        val = next((r_ for r_ in rows[i + 1:] if any('金額' in str(c) for c in r_ if c)), None)
        if not val: continue
        v = [float(c) for c in val if c not in (None, '') and '金額' not in str(c) and re.match(r'^-?[\d.]+$', str(c))]
        if len(v) < 56: continue
        b = lambda g: v[g * 4 + 2] / 1e5   # 1,000 yen -> 100 million yen
        vals = [b(4) + b(5), b(2) + b(3), b(12), b(8), b(7), b(0) + b(1), b(10), b(11)]
        a, z = re.search(r'(\d{8})_(\d{8})', u).groups()
        out.append([f'{int(a[4:6])}/{int(a[6:])}~{int(z[4:6])}/{int(z[6:])}', [round(x) for x in vals]])
    if out: save('jp_flows.json', out)
    return out or load('jp_flows.json', [])

def jgb(s):
    r = fetch(s, 'GET', 'https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv')
    if r is None: return load('jgb.json')
    txt = r.content.decode('shift_jis', errors='ignore')
    rows = [l.split(',') for l in txt.splitlines() if re.match(r'^R\d+\.', l)]
    if len(rows) < 2: return load('jgb.json')
    hdr = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '15', '20', '25', '30', '40']
    def era(x):
        y, mth, d = x.split('.'); return f'{2018 + int(y[1:])}-{int(mth):02d}-{int(d):02d}'
    last, prev = rows[-1], rows[-2]
    pick = lambda row, k: float(row[1 + hdr.index(k)]) if row[1 + hdr.index(k)] not in ('', '-') else None
    out = {'date': era(last[0]), 'items': []}
    for k, n in (('2', '2년'), ('5', '5년'), ('10', '10년'), ('30', '30년')):
        a, b = pick(last, k), pick(prev, k)
        out['items'].append({'n': n, 'v': a, 'chg': round((a - b) * 100, 1) if a is not None and b is not None else None})
    save('jgb.json', out)
    return out

TDNET = 'https://www.release.tdnet.info/inbs/'

def tdnet(s, ymd):
    rows = []
    for p in range(1, 41):
        r = fetch(s, 'GET', f'{TDNET}I_list_{p:03d}_{ymd}.html', ok=(200,), tries=2)
        if r is None: break
        r.encoding = 'utf-8'
        soup = BeautifulSoup(r.text, 'html.parser'); trs = soup.select('#main-list-table tr')
        if not trs: break
        for tr in trs:
            td = tr.find_all('td')
            if len(td) < 5: continue
            a = td[3].find('a')
            rows.append([td[0].get_text(strip=True), td[1].get_text(strip=True)[:4], td[2].get_text(strip=True), td[3].get_text(strip=True),
                         TDNET + a['href'] if a and a.get('href') else ''])
    log.info('tdnet %s %d', ymd, len(rows))
    return rows
