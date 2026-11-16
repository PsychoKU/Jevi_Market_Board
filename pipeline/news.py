"""Google News RSS headlines per market (links go to the original articles)."""
import re, email.utils, html
import xml.etree.ElementTree as ET
from common import session, fetch, log

QUERIES = {
 'kr': [('시황', '코스피 마감'), ('시황', '코스닥 마감'), ('수급', '외국인 순매수 코스피'), ('파생', '선물 옵션 외국인 만기'), ('금리·환율', '원달러 환율'),
        ('금리·환율', '국고채 금리'), ('산업', '반도체 주가'), ('산업', '2차전지 주가'), ('종목', '상한가'), ('전망', '증시 전망'), ('해외', '뉴욕증시 마감')],
 'jp': [('시황', '日経平均 終値'), ('시황', '東証 大引け'), ('금리·환율', '日銀 利上げ'), ('금리·환율', 'ドル円 為替'), ('종목', '上方修正 株価'),
        ('종목', 'ストップ高'), ('산업', '半導体 株 東証'), ('시황', '일본 증시 닛케이'), ('전망', '日本株 見通し')],
 'us': [('시황', 'stock market today S&P 500'), ('시황', 'Nasdaq close stocks'), ('금리·환율', 'Treasury yields Fed'), ('실적', 'earnings report shares'),
        ('산업', 'chip stocks Nvidia'), ('파생', 'options market VIX'), ('원자재', 'oil prices'), ('시황', '뉴욕증시'), ('전망', 'stock market week ahead')],
}
LANG = {'kr': ('ko', 'KR', 'KR:ko'), 'jp': ('ja', 'JP', 'JP:ja'), 'us': ('en-US', 'US', 'US:en')}

def headlines(m, per_query=4, window='2d'):
    s = session(); out, seen = [], set()
    for cat, q in QUERIES[m]:
        hl, gl, ce = ('ko', 'KR', 'KR:ko') if re.search('[가-힣]', q) else LANG[m]
        r = fetch(s, 'GET', 'https://news.google.com/rss/search', params={'q': f'{q} when:{window}', 'hl': hl, 'gl': gl, 'ceid': ce}, tries=2)
        if r is None: continue
        try: root = ET.fromstring(r.content)
        except ET.ParseError: continue
        n = 0
        for it in root.iter('item'):
            t = html.unescape(it.findtext('title') or ''); k = re.sub(r'\W', '', t)[:18]
            if not t or k in seen: continue
            seen.add(k); n += 1
            src = it.find('source')
            out.append({'title': re.sub(r'\s+-\s+[^-]+$', '', t).strip(), 'url': it.findtext('link'), 'src': src.text if src is not None else '',
                        'ts': email.utils.parsedate_to_datetime(it.findtext('pubDate')).timestamp() if it.findtext('pubDate') else 0, 'cat': cat})
            if n >= per_query: break
    out.sort(key=lambda x: -x['ts'])
    log.info('news %s %d', m, len(out))
    return out
