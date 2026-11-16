"""United States: SEC EDGAR current 8-K filings and Schedule 13D ownership reports."""
import os, re, time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from common import session, fetch, jget, log, load, save

SEC = 'https://www.sec.gov'
ATOM = '{http://www.w3.org/2005/Atom}'

def sec_session():
    # SEC requires a descriptive User-Agent with a contact address (kept in a repository secret, never in code)
    ua = os.environ.get('SEC_UA') or ''
    if '@' not in ua: return None
    s = session(); s.headers['User-Agent'] = f'JEVI market board {ua}'
    return s

def tickers(s):
    c = load('sec_tickers.json')
    if c and c.get('at', 0) > time.time() - 7 * 86400: return c['m']
    j = jget(s, SEC + '/files/company_tickers.json')
    m = {}
    for v in (j or {}).values(): m.setdefault(str(v['cik_str']), v['ticker'])
    if m: save('sec_tickers.json', {'at': time.time(), 'm': m})
    return m or (c or {}).get('m', {})

def feed(s, form, start):
    r = fetch(s, 'GET', f'{SEC}/cgi-bin/browse-edgar?action=getcurrent&type={form}&company=&dateb=&owner=include&start={start}&count=100&output=atom')
    if r is None: return []
    try: root = ET.fromstring(r.content)
    except ET.ParseError: return []
    out = []
    for e in root.findall(ATOM + 'entry'):
        g = lambda t: (e.find(ATOM + t).text or '') if e.find(ATOM + t) is not None else ''
        link = e.find(ATOM + 'link')
        out.append({'t': g('title'), 'u': link.get('href') if link is not None else '', 'd': g('updated'),
                    's': re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', g('summary'))).strip()})
    return out

def filings(s):
    if s is None: return load('us_sec.json', {'e8': [], 'd13': []})
    cik = tickers(s)
    e8 = []
    for st in range(0, 800, 100):
        f = feed(s, '8-K', st); e8 += f; time.sleep(0.2)
        if len(f) < 100: break
    out8 = []
    for e in e8:
        m = re.match(r'^8-K(?:/A)? - (.*) \((\d+)\)', e['t'])
        out8.append({'d': e['d'], 'nm': m.group(1) if m else e['t'], 'tk': cik.get(str(int(m.group(2)))) if m else None,
                     'items': re.findall(r'Item \d+\.\d+: .+?(?= Item |$)', e['s']), 'u': e['u'], 'a': '8-K/A' in e['t']})
    e13 = []
    for form in ('SCHEDULE 13D', 'SCHEDULE 13D/A'):
        for st in range(0, 300, 100):
            f = feed(s, form.replace(' ', '%20'), st); e13 += f; time.sleep(0.2)
            if len(f) < 100: break
    by = {}
    for e in e13:
        acc = re.search(r'AccNo: ([\d-]+)', e['s']); m = re.match(r'^(.*?) - (.*) \((\d+)\) \((Subject|Filed by|Reporting)\)', e['t'])
        if not acc or not m: continue
        o = by.setdefault(acc.group(1), {'acc': acc.group(1), 'form': m.group(1), 'd': e['d'], 'u': e['u']})
        if m.group(4) == 'Subject': o.update(subj=m.group(2), tk=cik.get(str(int(m.group(3)))), u=e['u'])
        else: o.setdefault('filer', m.group(2))
    d13 = list(by.values())
    def pct(o):
        r = fetch(s, 'GET', o['u'], tries=2)
        if r is None: return
        x = next((h for h in re.findall(r'href="([^"]+primary_doc\.xml)"', r.text) if 'xsl' not in h), None)
        if not x: return
        rr = fetch(s, 'GET', SEC + x if x.startswith('/') else x, tries=2)
        if rr is None: return
        p = [float(v) for v in re.findall(r'<(?:\w+:)?(?:percentOfClass|classPercent)>\s*([\d.]+)', rr.text)]
        o['pct'] = max(p) if p else None
        time.sleep(0.15)
    with ThreadPoolExecutor(3) as ex: list(ex.map(pct, d13))
    out = {'e8': out8, 'd13': d13}
    save('us_sec.json', out)
    log.info('sec 8-K %d, 13D %d', len(out8), len(d13))
    return out
