"""Assemble site/data/{market}.json from cached raw data (state/) and Claude-written commentary (content/)."""
import re, json, unicodedata, datetime as dt, time
from common import load, save, OUT, CONTENT, DIGEST, r2, TZ, market_date, log
from yahoo import build_row, COLS as YCOLS
from jp_disc_rules import translate as jp_translate
import derive

nf = lambda s: unicodedata.normalize('NFKC', s or '').strip()
SEC_KO = {'Technology': '기술', 'Financial Services': '금융', 'Healthcare': '헬스케어', 'Consumer Cyclical': '경기소비재', 'Industrials': '산업재',
          'Communication Services': '커뮤니케이션', 'Consumer Defensive': '필수소비재', 'Energy': '에너지', 'Basic Materials': '소재', 'Real Estate': '부동산',
          'Utilities': '유틸리티', '': '기타', None: '기타'}
JP33 = {'水産・農林業': '수산·농림', '鉱業': '광업', '建設業': '건설', '食料品': '식료품', '繊維製品': '섬유', 'パルプ・紙': '펄프·종이', '化学': '화학', '医薬品': '의약품',
        '石油・石炭製品': '석유·석탄', 'ゴム製品': '고무', 'ガラス・土石製品': '유리·토석', '鉄鋼': '철강', '非鉄金属': '비철금속', '金属製品': '금속제품', '機械': '기계',
        '電気機器': '전기기기', '輸送用機器': '수송용기기', '精密機器': '정밀기기', 'その他製品': '기타제품', '電気・ガス業': '전기·가스', '陸運業': '육운', '海運業': '해운',
        '空運業': '항공', '倉庫・運輸関連業': '창고·운수', '情報・通信業': '정보·통신', '卸売業': '도매(상사)', '小売業': '소매', '銀行業': '은행', '証券、商品先物取引業': '증권·선물',
        '保険業': '보험', 'その他金融業': '기타금융', '不動産業': '부동산', 'サービス業': '서비스'}
JSEG = {'プライム': '프라임', 'スタンダード': '스탠다드', 'グロース': '그로스'}
JSZ = {'TOPIX Core30': 'Core30', 'TOPIX Large70': 'Large70', 'TOPIX Mid400': 'Mid400'}
WICS = json.loads('{"반도체와반도체장비":"반도체","디스플레이장비및부품":"디스플레이","전자장비와기기":"전자장비","소프트웨어":"소프트웨어","IT서비스":"IT서비스","핸드셋":"휴대폰","통신장비":"통신장비","컴퓨터와주변기기":"컴퓨터","디스플레이패널":"디스플레이","전기제품":"전기제품","전자제품":"전자제품","에너지장비및서비스":"에너지장비","석유와가스":"석유·가스","화학":"화학","건축자재":"건축자재","건축제품":"건축제품","금속및광물":"금속·광물","철강":"철강","비철금속":"비철금속","종이와목재":"종이·목재","포장재":"포장재","조선":"조선","우주항공과국방":"방산·우주","기계":"기계","전기장비":"전기장비","복합기업":"지주·복합","건설":"건설","무역회사와판매업체":"상사","상업서비스와공급품":"상업서비스","항공화물운송과물류":"물류","항공사":"항공","해운사":"해운","도로와철도운송":"육운","운송인프라":"운송인프라","자동차":"자동차","자동차부품":"자동차부품","가정용기기와용품":"가정용품","레저용장비와제품":"레저용품","섬유,의류,신발,호화품":"의류·패션","호텔,레스토랑,레저":"호텔·레저","교육서비스":"교육","다각화된소비자서비스":"소비자서비스","판매업체":"유통","인터넷과카탈로그소매":"이커머스","백화점과일반상점":"백화점","전문소매":"전문소매","미디어":"미디어","방송과엔터테인먼트":"엔터·방송","양방향미디어와서비스":"인터넷","게임엔터테인먼트":"게임","광고":"광고","출판":"출판","다각화된통신서비스":"통신","무선통신서비스":"통신","식품":"식품","음료":"음료","담배":"담배","화장품":"화장품","가정용품":"생활용품","식품과기본식료품소매":"식품유통","건강관리장비와용품":"의료기기","건강관리업체및서비스":"헬스케어서비스","건강관리기술":"헬스케어IT","생물공학":"바이오","제약":"제약","생명과학도구및서비스":"바이오서비스","은행":"은행","증권":"증권","창업투자":"창투","손해보험":"손해보험","생명보험":"생명보험","기타금융":"기타금융","카드":"카드","부동산":"부동산","전기유틸리티":"전력","가스유틸리티":"가스","복합유틸리티":"유틸리티","독립전력생산및에너지거래":"에너지"}')
COLS = ['code', 'nm', 'en', 'sec', 'seg', 'px', 'chg', 'mcap', 'to', 'fh', 'fl', 'c52', 'pe', 'pb', 'dy', 'nh', 'nl', 'a200', 'a50', 'vr',
        'r1w', 'r4w', 'r12w', 'ytd', 'nhd', 'hi', 'lo', 'fpe', 'sz', 'fr', 'sum']
LOCAL = {'jp': 9, 'kr': 9}

def fmt_local(ts, m, with_date=True):
    if not ts: return ''
    d = dt.datetime.fromtimestamp(ts, TZ[m])
    return d.strftime('%m/%d %H:%M' if with_date else '%H:%M')

# ---------------- stock rows ----------------
def stock_rows(m):
    raw = load(f'{m}_raw.json', {}); sec = load(f'{m}_sector.json', {}); ext = load(f'{m}_ext.json', {})
    hist = load(f'{m}_hist.json', {}); loc = load(f'{m}_loc.json', {}); mdate = market_date(m)
    jpx = load('jpx_list.json', {}).get('m', {}) if m == 'jp' else {}
    kd = load('kr_daum.json', {}) if m == 'kr' else {}; knm = load('kr_names.json', {}) if m == 'kr' else {}
    if m == 'kr':
        for c in list(knm):
            base = c[:-1] + '0'
            if c[-1] != '0' and base in kd and (c not in kd or not kd[c][3]):
                kd[c] = (kd.get(c) or [None, None, None, None, None, None, None])[:3] + [kd[base][3], kd[base][4]] + [None, kd[base][6]]
    out = []
    for sym, q in raw.items():
        r = build_row(m, sym, q, sec.get(sym), ext.get(sym), hist.get(sym), loc.get(sym), mdate)
        if not r: continue
        o = dict(zip(YCOLS, r)); o.update(sz='', fr=None, sum='')
        o['nm'], o['en'] = nf(o['nm']), nf(o['en'])
        if m == 'jp':
            j = jpx.get(o['code'])
            if not j or 'PRO' in j[0]: continue
            o['seg'], o['sec'], o['sz'] = JSEG.get(j[0], j[0]), JP33.get(j[1], j[1]), JSZ.get(j[2], '')
        elif m == 'kr':
            if o['code'] not in knm: continue
            k = kd.get(o['code'])
            o['sec'] = WICS.get(k[3], k[3]) if k and k[3] else SEC_KO.get(o['sec'], '기타')
            if k:
                if k[0] is not None: o['pe'] = round(k[0], 1)
                if k[1] is not None: o['pb'] = round(k[1], 2)
                if k[2] and o['px']: o['dy'] = round(k[2] / o['px'] * 100, 2)
                o['fr'], o['sum'] = k[5], k[6]
            o['nm'] = knm[o['code']]
        else:
            if not o['mcap']: continue
            o['sec'] = SEC_KO.get(o['sec'], '기타')
            o['seg'] = {'NYSE American': '기타', 'NYSE Arca': '기타', 'Cboe': '기타'}.get(o['seg'], o['seg'])
        if o['pe'] is not None and (o['pe'] <= 0 or o['pe'] > 3000): o['pe'] = None
        if o['pb'] is not None and o['pb'] <= 0: o['pb'] = None
        out.append([o.get(c) for c in COLS])
    return out

# ---------------- market snapshot (strip, charts, macro) ----------------
STRIP = {
 'jp': [('^N225', '닛케이225', None, True), ('1306.T', 'TOPIX (1306 ETF)'), ('1591.T', 'JPX400 (1591 ETF)'), ('2516.T', '그로스250 (2516 ETF)'), ('NIY=F', '닛케이 선물 (엔, CME)'), ('JPY=X', 'USD/JPY', 'fx'), ('^GSPC', 'S&P 500'), ('^KS11', 'KOSPI')],
 'kr': [('^KS11', 'KOSPI', None, True), ('^KQ11', 'KOSDAQ', None, True), ('^KS200', 'KOSPI 200'), ('KRW=X', 'USD/KRW', 'fx'), ('ES=F', 'S&P500 선물'), ('^SOX', '필라델피아 반도체'), ('^N225', '닛케이225'), ('^TNX', '미 국채 10년', 'y')],
 'us': [('^GSPC', 'S&P 500', None, True), ('^IXIC', '나스닥 종합', None, True), ('^DJI', '다우'), ('^RUT', '러셀 2000'), ('^SOX', '필라델피아 반도체'), ('^VIX', 'VIX'), ('ES=F', 'S&P500 선물'), ('NQ=F', '나스닥100 선물'), ('^TNX', '미 국채 10년', 'y')],
}
CHARTS = {'jp': [('^N225', '닛케이225', '닛케이')], 'kr': [('^KS11', '코스피', 'KOSPI'), ('^KQ11', '코스닥', 'KOSDAQ')], 'us': [('^GSPC', 'S&P 500', 'S&P'), ('^IXIC', '나스닥 종합', '나스닥')]}
BENCH = {'jp': ('1306.T', 'TOPIX'), 'kr': ('^KS11', '코스피'), 'us': ('^GSPC', 'S&P500')}
MACRO = [('일본', '^N225', '닛케이225'), ('일본', '1306.T', 'TOPIX (1306 ETF)'), ('일본', 'NIY=F', '닛케이 선물 (엔, CME)'), ('한국', '^KS11', 'KOSPI'), ('한국', '^KQ11', 'KOSDAQ'),
         ('미국', '^GSPC', 'S&P 500'), ('미국', '^IXIC', '나스닥 종합'), ('미국', '^DJI', '다우'), ('미국', '^SOX', '필라델피아 반도체'), ('미국', '^VIX', 'VIX'), ('미국', 'ES=F', 'S&P500 선물'),
         ('아시아', '^HSI', '항셍'), ('아시아', '000001.SS', '상하이종합'), ('아시아', '^TWII', '대만 가권'), ('유럽', '^STOXX50E', '유로스톡스50'), ('유럽', '^GDAXI', 'DAX'),
         ('유럽', '^FTSE', 'FTSE100'), ('환율', 'JPY=X', 'USD/JPY'), ('환율', 'KRW=X', 'USD/KRW'), ('환율', 'EURUSD=X', 'EUR/USD'), ('환율', 'DX-Y.NYB', '달러 인덱스'),
         ('금리', '^TNX', '미 국채 10년'), ('금리', '^TYX', '미 국채 30년'), ('원자재', 'CL=F', 'WTI'), ('원자재', 'BZ=F', '브렌트'), ('원자재', 'GC=F', '금'),
         ('원자재', 'HG=F', '구리'), ('가상자산', 'BTC-USD', '비트코인')]
MACRO_SYMS = sorted({s for _, s, _ in MACRO} | {t[0] for v in STRIP.values() for t in v} | {'^KS200', '^RUT', 'NQ=F', '^FVX', '^IRX'})

def market_parts(m):
    mk = load('market.json', {}); QQ = mk.get('q', {}); CH = mk.get('ch', {})
    def si(t):
        q = QQ.get(t[0])
        if not q: return None
        return {'n': t[1], 'px': q.get('regularMarketPrice'), 'ch': q.get('regularMarketChange'), 'chg': q.get('regularMarketChangePercent'),
                'fmt': t[2] if len(t) > 2 else None, 'home': len(t) > 3 and t[3]}
    strip = [x for x in map(si, STRIP[m]) if x]
    charts = []
    for s, n, short in CHARTS[m]:
        q = QQ.get(s); c = CH.get(s)
        if q and c and c.get('d') and c.get('y'):
            charts.append({'n': n, 'short': short, 'px': q.get('regularMarketPrice'), 'ch': q.get('regularMarketChange'), 'chg': q.get('regularMarketChangePercent'), 'd': c['d'], 'y': {'pts': c['y']['pts']}})
    macro = []
    for g, s, n in MACRO:
        q = QQ.get(s)
        if q and q.get('regularMarketPrice') is not None:
            macro.append({'g': g, 'n': n, 'px': q['regularMarketPrice'], 'chg': q.get('regularMarketChangePercent'), 't': fmt_local(q.get('regularMarketTime'), 'kr'), 'y': g == '금리'})
    bs, bn = BENCH[m]; b = (mk.get('bench') or {}).get(bs) or {}
    return strip, charts, macro, {'n': bn, 'r1w': b.get('r1w'), 'r4w': b.get('r4w'), 'r12w': b.get('r12w')}

# ---------------- disclosures ----------------
KR_SKIP = r'대량보유|임원ㆍ주요주주|투자설명서|일괄신고|ETF|ETN|ELW|회전율|소수계좌|변동성 상위|거래량 증가율|종가급변|최근 20일중|특정계좌|대량매매내역|자기주식매매|참고서류|증권발행실적|증권 발행결과|추가상장|가격제한폭|유동성공급자|신주인수권증서|매매거래정지기간변경'
def kr_cat(t):
    for c, p in [('실적·전망', r'영업\(잠정\)|영업실적|매출액|손익구조|결산실적|기업설명회|실적'), ('수주·계약', r'단일판매|공급계약'),
                 ('주주환원', r'자기주식|현금ㆍ현물배당|배당|기업가치 제고|소각'), ('자금조달', r'유상증자|전환사채|신주인수권부|교환사채|사채|전환가액|증자'),
                 ('M&A·지배구조', r'최대주주|합병|분할|양수|양도|타법인주식|공개매수|주식병합|담보제공|채무보증'),
                 ('시장조치', r'공매도|투자경고|투자주의|단기과열|불성실|조회공시|해명|거래정지|매매거래|관리종목|상장폐지|정지'),
                 ('리스크', r'소송|횡령|배임|회계처리기준|감사의견|검찰')]:
        if re.search(p, t): return c
    return '기타'

def disc(m, rows):
    cols = {c: i for i, c in enumerate(COLS)}; codes = {r[0] for r in rows}
    caps = {r[0]: r[cols['mcap']] for r in rows}; names = {r[1].replace(' ', ''): r[0] for r in rows}
    items = []
    if m == 'jp':
        T = load('jp_tdnet.json', {}); day = T.get('date', '')
        for r in T.get('rows', []):
            if r[1] not in codes: continue
            ko, cat, flag = jp_translate(r[3])
            if cat == '실적·전망' and '결산 단신' in ko and (caps.get(r[1]) or 0) >= 10000: flag = 1
            items.append({'t': r[0], 'code': r[1], 'nm': nf(r[2]), 'title': ko, 'orig': nf(r[3]), 'cat': cat, 'flag': flag, 'url': r[4]})
        cats = [c for c in ['실적·전망', '주주환원', 'M&A·지배구조', '지분·지배구조', '자금조달', '시장구분', '리스크', '인사·기타', '정정', '기타'] if any(i['cat'] == c for i in items)]
        return {'title': 'TDnet 적시공시', 'sub': f'{day} · 상장 종목 공시만 (ETF·REIT 제외)', 'cats': cats, 'items': items,
                'note': '제목은 공시 유형별 규칙으로 한국어로 옮기고 원문을 함께 적었습니다(유형을 알 수 없는 일부는 원문 그대로). ● 표시는 실적 전망 수정·증배·자사주 매입·M&A·사고 공시와 시가총액 1조엔 이상 기업의 결산 단신입니다.'}
    if m == 'kr':
        K = load('kr_kind.json', {}); day = K.get('date', '')
        for r in K.get('today', []):
            t = r[3]
            if re.search(KR_SKIP, t): continue
            flag = 1 if re.search(r'영업\(잠정\)|단일판매|유상증자|전환사채|최대주주 변경|합병|자기주식취득|기업가치 제고|조회공시|해명|소송|회계처리기준|매매거래정지|공개매수', t) else 0
            items.append({'t': r[0][11:16], 'code': names.get(r[2].replace(' ', ''), ''), 'nm': r[2], 'title': t, 'orig': None, 'cat': kr_cat(t), 'flag': flag,
                          'url': f'https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno={r[4]}'})
        cats = [c for c in ['실적·전망', '수주·계약', '주주환원', '자금조달', 'M&A·지배구조', '시장조치', '리스크', '기타'] if any(i['cat'] == c for i in items)]
        return {'title': 'KIND 공시', 'sub': f'{day} · 한국거래소 공시 (지분 보고·채권 서류·시장 통계 제외)', 'cats': cats, 'items': items,
                'note': '출처는 한국거래소 KIND입니다. ● 표시는 잠정실적·공급계약·증자·최대주주 변경·조회공시처럼 주가 영향이 큰 공시입니다.'}
    S = load('us_sec.json', {'e8': []})
    IK = {'1.01': '주요 계약 체결', '1.02': '주요 계약 해지', '1.05': '사이버보안 사고', '2.01': '자산 인수·처분 완료', '2.02': '실적 발표', '2.03': '채무 발생', '2.05': '구조조정 비용',
          '2.06': '자산 손상', '3.01': '상장 유지 기준 미달·이전', '3.02': '비등록 증권 발행', '3.03': '주주 권리 변경', '4.01': '감사인 교체', '4.02': '재무제표 신뢰 불가',
          '5.01': '지배권 변경', '5.02': '임원·이사 변동', '5.03': '정관 변경', '5.07': '주주총회 결과', '5.08': '주주 추천 이사 일정', '7.01': '공정공시(Reg FD)', '8.01': '기타 중요 사항', '9.01': '재무제표·첨부'}
    CAT = [('실적·전망', ['2.02']), ('M&A·자산', ['2.01', '1.01', '1.02', '5.01']), ('자금조달', ['2.03', '3.02']), ('임원·지배구조', ['5.02', '5.03', '5.07', '5.08', '3.03']),
           ('리스크', ['1.05', '2.05', '2.06', '3.01', '4.01', '4.02']), ('공정공시·기타', ['7.01', '8.01'])]
    for e in S['e8']:
        tk = e.get('tk')
        if not tk or tk not in codes: continue
        nums = [n for n in (re.match(r'Item (\d+\.\d+)', x).group(1) for x in e['items'] if re.match(r'Item (\d+\.\d+)', x)) if n != '9.01'] or ['9.01']
        cat = next((c for c, ks in CAT if any(n in ks for n in nums)), '공정공시·기타')
        flag = 1 if any(n in ('1.01', '2.01', '5.01', '1.05', '4.02', '2.05', '2.06') for n in nums) or ('2.02' in nums and (caps.get(tk) or 0) >= 10000) else 0
        d = dt.datetime.fromisoformat(e['d'])
        items.append({'t': d.strftime('%m/%d %H:%M'), 'code': tk, 'nm': e['nm'], 'title': ' · '.join(IK.get(n, 'Item ' + n) for n in nums) + (' (정정)' if e.get('a') else ''),
                      'orig': ', '.join(nums), 'cat': cat, 'flag': flag, 'url': e['u']})
    cats = [c for c, _ in CAT if any(i['cat'] == c for i in items)]
    return {'title': 'SEC 8-K 수시공시', 'sub': '최근 제출분 (미 동부시각) · 상장 종목만', 'cats': cats, 'items': items,
            'note': 'SEC EDGAR의 8-K 제출 목록입니다. 제목은 8-K 항목 번호를 한국어로 옮긴 것입니다. ● 표시는 주요 계약·인수·사이버 사고·손상 항목과 시가총액 100억 달러 이상 기업의 실적 발표입니다.'}

def holdings(m, rows):
    if m == 'jp':
        return {'title': '대량보유 보고서 (EDINET)', 'items': [], 'empty': '일본의 대량보유 보고서는 금융청 EDINET API 키가 있어야 받을 수 있습니다. 저장소 비밀 값에 EDINET_API_KEY를 넣으면 다음 갱신부터 채워집니다.'}
    if m == 'kr':
        K = load('kr_kind.json', {}); names = {r[1].replace(' ', ''): r[0] for r in rows}; items = []
        for r in K.get('big', []):
            t = r[3]; kind = '정정' if '[정정]' in t else ('일반' if '일반' in t else ('약식' if '약식' in t else '기타'))
            items.append({'d': r[0][5:].replace('-', '/'), 'code': names.get(r[2].replace(' ', ''), ''), 'nm': r[2], 'holder': r[5], 'a': None, 'b': None,
                          'p': {'일반': '일반 (경영참여 가능)', '약식': '약식 (단순·일반투자)', '정정': '정정', '기타': ''}[kind],
                          'url': f'https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno={r[4]}'})
        items.sort(key=lambda z: z['d'], reverse=True)
        st = [{'n': '보고 건수', 'v': len(items)}, {'n': '일반 (경영참여 가능)', 'v': sum(1 for i in items if i['p'].startswith('일반'))},
              {'n': '약식 (단순투자 등)', 'v': sum(1 for i in items if i['p'].startswith('약식'))}, {'n': '정정 보고', 'v': sum(1 for i in items if i['p'] == '정정')}]
        return {'title': '주식등의 대량보유상황 보고 (5% 룰)', 'sub': '최근 10거래일 · 출처 KIND', 'stats': st, 'items': items,
                'note': '보유 비율이 5% 이상이 되거나 1%p 이상 바뀌면 5영업일 안에 내는 보고입니다. 일반 보고는 경영 참여 목적일 수 있고, 약식은 단순·일반 투자 목적입니다. 보유 비율 수치는 DART 원문에서 확인할 수 있습니다.'}
    S = load('us_sec.json', {'d13': []}); items = []
    for o in S['d13']:
        d = dt.datetime.fromisoformat(o['d'])
        items.append({'d': d.strftime('%m/%d %H:%M'), 'code': o.get('tk') or '', 'nm': o.get('subj') or '', 'holder': o.get('filer') or '', 'a': o.get('pct'), 'b': None,
                      'p': '13D 신규 (경영 관여 가능)' if o['form'] in ('SCHEDULE 13D', 'SC 13D') else '13D/A 변경', 'url': o['u']})
    items.sort(key=lambda z: z['d'], reverse=True)
    st = [{'n': '13D 보고', 'v': len(items)}, {'n': '신규 13D', 'v': sum(1 for i in items if '신규' in i['p'])},
          {'n': '변경 13D/A', 'v': sum(1 for i in items if '변경' in i['p'])}, {'n': '10% 이상 보유', 'v': sum(1 for i in items if (i['a'] or 0) >= 10)}]
    return {'title': 'Schedule 13D 지분 보고', 'sub': '최근 제출분 · 출처 SEC EDGAR', 'stats': st, 'items': items,
            'note': '5% 이상을 보유하면서 경영에 영향을 줄 의도가 있으면 내는 보고(13D)입니다. 지분율은 표지의 Percent of class 값 중 가장 큰 값입니다.'}

# ---------------- flows & rates ----------------
JP_FLOW_N = ['해외투자자', '개인', '신탁은행 (연기금)', '사업법인 (자사주 등)', '투자신탁', '증권 자기매매', '생보·손보', '도시·지방은행']
def flows(m, rows):
    if m == 'jp':
        F = load('jp_flows.json', [])
        return {'title': '투자주체별 순매수', 'unit': '억엔', 'blocks': [{'tab': w, 'sub': f'{w} 주간 · 도쿄·나고야 2시장', 'items': [{'n': n, 'v': v} for n, v in zip(JP_FLOW_N, vals)]} for w, vals in F],
                'note': '출처: 일본거래소그룹(JPX) 투자부문별 매매 동향(주간, 금액 기준). 순매수 = 매수 − 매도. 매주 넷째 영업일에 전주 분이 공표됩니다.'}
    if m == 'kr':
        inv = load('kr_investors.json', {}); blocks = []
        f = lambda r, k: round((r.get(k) or 0) / 1e8)
        for mkt, lab in (('KOSPI', '코스피'), ('KOSDAQ', '코스닥')):
            rs = sorted(inv.get(mkt) or [], key=lambda x: x['date'])
            if not rs: continue
            last = rs[-1]
            blocks.append({'tab': lab + ' 당일', 'sub': last['date'][:10] + ' 일간',
                           'items': [{'n': '외국인', 'v': f(last, 'foreignStraightPurchasePrice')}, {'n': '기관', 'v': f(last, 'institutionStraightPurchasePrice')}, {'n': '개인', 'v': f(last, 'individualStraightPurchasePrice')}]})
            r20 = rs[-20:]
            blocks.append({'tab': lab + ' 20일', 'sub': f"{r20[0]['date'][5:10].replace('-', '/')}~{r20[-1]['date'][5:10].replace('-', '/')} 누적",
                           'items': [{'n': n, 'v': sum(f(r, k) for r in r20)} for n, k in (('외국인', 'foreignStraightPurchasePrice'), ('기관', 'institutionStraightPurchasePrice'), ('개인', 'individualStraightPurchasePrice'))],
                           'series': {'label': lab + ' 최근 60거래일', 'dates': [r['date'][:10] for r in rs],
                                      'data': {n: [f(r, k) for r in rs] for n, k in (('외국인', 'foreignStraightPurchasePrice'), ('기관', 'institutionStraightPurchasePrice'), ('개인', 'individualStraightPurchasePrice'))}}})
        return {'title': '투자자별 순매수', 'unit': '억원', 'blocks': blocks, 'note': '출처: 다음 금융(한국거래소 집계). 장중에는 잠정치입니다.'}
    I = {c: i for i, c in enumerate(COLS)}; agg = {}
    for r in rows:
        to = r[I['to']] or 0; ch = r[I['chg']] or 0
        agg[r[I['sec']]] = agg.get(r[I['sec']], 0) + (to if ch > 0 else -to if ch < 0 else 0)
    items = sorted([{'n': k, 'v': round(v)} for k, v in agg.items()], key=lambda z: -z['v'])
    return {'title': '섹터별 자금 쏠림 (추정)', 'unit': '$M', 'blocks': [{'tab': '당일', 'sub': '상승 종목 대금 − 하락 종목 대금', 'items': items}],
            'note': '미국은 투자자 유형별 매매 동향이 매일 공표되지 않아, 업종별로 오른 종목 거래대금에서 내린 종목 거래대금을 뺀 값을 대용치로 씁니다.'}

def rates(m):
    QQ = load('market.json', {}).get('q', {})
    g = lambda s: (QQ.get(s, {}).get('regularMarketPrice'), (QQ.get(s, {}).get('regularMarketChange') or 0) * 100)
    if m == 'jp':
        J = load('jgb.json') or {'date': '', 'items': []}
        return {'title': '일본 국채 금리', 'asof': f"{J['date']} 기준 (재무성)", 'items': [i for i in J['items'] if i['v'] is not None],
                'note': '재무성 국채 금리 정보입니다. 다음 영업일에 공표됩니다. 변화는 전 영업일 대비 bp입니다.'}
    if m == 'kr':
        J = load('jgb.json') or {'items': []}; j10 = next((i for i in J['items'] if i['n'] == '10년'), None)
        items = [{'n': '미 국채 10년', 'v': g('^TNX')[0], 'chg': g('^TNX')[1]}, {'n': '미 국채 30년', 'v': g('^TYX')[0], 'chg': g('^TYX')[1]}]
        if j10: items.append({'n': '일본 국채 10년', 'v': j10['v'], 'chg': j10['chg']})
        return {'title': '금리', 'asof': '미 국채 실시간 · 일본은 재무성', 'items': [i for i in items if i['v'] is not None],
                'note': '국고채 금리는 무료로 자동 수집할 공식 출처가 없어 해설에서 다룹니다.'}
    items = [{'n': n, 'v': g(s)[0], 'chg': g(s)[1]} for n, s in (('13주', '^IRX'), ('5년', '^FVX'), ('10년', '^TNX'), ('30년', '^TYX'))]
    return {'title': '미 국채 금리', 'asof': '실시간 (Yahoo)', 'items': [i for i in items if i['v'] is not None], 'note': 'CBOE 국채 금리 지수 기준이며 변화는 전일 대비 bp입니다.'}

# ---------------- news ----------------
def news(m, content):
    tr = content.get('news_tr', {}); out = []
    for x in load(f'{m}_news.json', []):
        t = tr.get(x['url'])
        title, orig, cat = (t[0], x['title'], t[1] if len(t) > 1 else x['cat']) if t else (x['title'], None, x['cat'])
        codes = []
        for k, v in (content.get('news_codes') or {}).items():
            if k in x['title']: codes += v if isinstance(v, list) else [v]
        out.append({'t': fmt_local(x['ts'], 'kr' if m != 'us' else 'us'), 'cat': cat, 'title': title, 'orig': orig, 'src': x['src'], 'url': x['url'], 'codes': codes})
    cats = []
    for z in out:
        if z['cat'] not in cats: cats.append(z['cat'])
    return {'sub': '최근 이틀 · 제목을 누르면 원문 (번역은 매일 해설 갱신 때 반영)', 'cats': cats, 'items': out}

# ---------------- estimates ----------------
def estimates(m, rows):
    E = load(f'{m}_est.json')
    if not E: return None
    I = {c: i for i, c in enumerate(COLS)}
    by = {r[0]: {'nm': r[1], 'sec': r[3], 'mcap': r[I['mcap']], 'r1w': r[I['r1w']], 'pe': r[I['pe']]} for r in rows}
    code_of = (lambda s: s) if m == 'us' else (lambda s: s.rsplit('.', 1)[0])
    comp = derive.estimate_rows(E['est'], E['universe'], by, code_of)
    def pack(c):
        f = lambda b: [b['end'], r2(b['eps'], 3), r2(b['r1w'], 2), r2(b['r4w'], 2), r2(b['r12w'], 2), b['n'], b['up7'], b['dn7'], b['up30'], b['dn30']] if b else None
        return [c['code'], c['nm'], c['sec'], c['mcap'], r2(c['px'], 2), c['chg1w'], f(c['y0']), f(c['y1']), r2(c['tp'], 2), r2(c['rec'], 2), c['na']]
    secs = {k: derive.sector_revisions(comp, k) for k in ('y0', 'y1')}
    def stat(k):
        v = [c[k]['r1w'] for c in comp if c.get(k) and c[k]['r1w'] is not None]
        up = sum(1 for x in v if x > 0.05); dn = sum(1 for x in v if x < -0.05)
        return {'n': len(v), 'up': up, 'dn': dn, 'breadth': round((up - dn) / len(v) * 100, 1) if v else None}
    return {'asof': E.get('asof', ''), 'n': len(comp), 'stats': {'y0': stat('y0'), 'y1': stat('y1')}, 'sectors': secs,
            'cols': ['code', 'nm', 'sec', 'mcap', 'px', 'r1w_px', 'y0', 'y1', 'tp', 'rec', 'na'],
            'est_cols': ['end', 'eps', 'r1w', 'r4w', 'r12w', 'n', 'up7', 'dn7', 'up30', 'dn30'], 'rows': [pack(c) for c in comp]}

# ---------------- derivatives ----------------
def deriv_us():
    mk = load('market.json', {}); QQ = mk.get('q', {}); O = load('us_options.json', {})
    vix = []
    for s, n in (('^VIX9D', 'VIX 9일'), ('^VIX', 'VIX 1개월'), ('^VIX3M', 'VIX 3개월'), ('^VIX6M', 'VIX 6개월'), ('^VVIX', 'VVIX (VIX의 변동성)'),
                 ('^SKEW', 'SKEW (꼬리 위험)'), ('^VXN', 'VXN (나스닥100)'), ('^OVX', 'OVX (원유)'), ('^MOVE', 'MOVE (미 국채)')):
        q = QQ.get(s)
        if q: vix.append({'s': s, 'n': n, 'px': q.get('regularMarketPrice'), 'chg': q.get('regularMarketChangePercent')})
    now = time.time(); opts = []
    for s in ('SPY', 'QQQ', 'IWM', 'NVDA', 'TSLA'):
        o = O.get(s)
        if o and o.get('exps'):
            try: opts.append(derive.option_metrics(s, o, o.get('ts') or now))
            except Exception as e: log.warning('option metrics %s: %s', s, e)
    return {'vix': vix, 'vixh': mk.get('vixh', {}), 'opts': opts, 'asof': fmt_local(max([o.get('ts') or 0 for o in O.values()] or [0]), 'us')}

def deriv_kr():
    snaps = load('kis_today.json', {}).get('snaps', [])
    if not snaps:
        return {'kis': False, 'note': '한국투자증권 Open API 키(KIS_APP_KEY, KIS_APP_SECRET)를 저장소 비밀 값에 넣으면 선물·콜·풋 투자자별 순매수와 옵션 미결제 분포가 표시됩니다.'}
    last = snaps[-1]
    series = {'t': [fmt_local(s['ts'], 'kr', False) for s in snaps]}
    for prod in ('fut', 'call', 'put'):
        for inv in ('foreign', 'indiv', 'inst'):
            series[f'{prod}_{inv}'] = [((s['flows'].get(prod) or {}).get(inv) or {}).get('amt') for s in snaps]
            series[f'{prod}_{inv}_q'] = [((s['flows'].get(prod) or {}).get(inv) or {}).get('qty') for s in snaps]
    opt = None
    if last.get('opt'):
        o = last['opt']; spot = None
        mk = load('market.json', {}).get('q', {}); spot = (mk.get('^KS200') or {}).get('regularMarketPrice') or (last.get('fut') or {}).get('px')
        calls = [r for r in o['calls'] if r[0]]; puts = [r for r in o['puts'] if r[0]]
        coi = sum(r[1] or 0 for r in calls); poi = sum(r[1] or 0 for r in puts)
        mp = derive.max_pain([[r[0], r[1] or 0] for r in calls], [[r[0], r[1] or 0] for r in puts]) if calls and puts else None
        ks = sorted({r[0] for r in calls + puts if spot and abs(r[0] / spot - 1) <= 0.12}) if spot else sorted({r[0] for r in calls + puts})
        cm = {r[0]: r for r in calls}; pm = {r[0]: r for r in puts}
        gex = sum(((cm[k][5] or 0) * (cm[k][1] or 0) if k in cm else 0) - ((pm[k][5] or 0) * (pm[k][1] or 0) if k in pm else 0) for k in ks) * 250000 * (spot or 0) ** 2 * 0.01 if spot else None
        cw = max((r for r in calls if spot and r[0] >= spot and r[0] in ks), key=lambda r: r[1] or 0, default=None)
        pw = max((r for r in puts if spot and r[0] <= spot and r[0] in ks), key=lambda r: r[1] or 0, default=None)
        opt = {'month': o['month'], 'spot': spot, 'pcr_oi': round(poi / coi, 2) if coi else None, 'mp': mp, 'cw': cw[0] if cw else None, 'pw': pw[0] if pw else None,
               'gex': round(gex / 1e8) if gex is not None else None,
               'oi': [[k, (cm.get(k) or [0, 0, 0])[1] or 0, (pm.get(k) or [0, 0, 0])[1] or 0, (cm.get(k) or [0, 0, 0])[2] or 0, (pm.get(k) or [0, 0, 0])[2] or 0] for k in ks]}
    return {'kis': True, 'asof': fmt_local(last['ts'], 'kr'), 'flows': last['flows'], 'fut': last.get('fut'), 'program': last.get('program'), 'series': series, 'opt': opt,
            'note': '출처: 한국투자증권 Open API. 금액은 백만원 단위 원자료를 억원으로 바꿔 표시합니다. 장중 값은 누적 잠정치이고, 15분마다 쌓은 값으로 흐름을 그립니다.'}

def deriv(m):
    if m == 'us': return deriv_us()
    if m == 'kr': return {**deriv_kr(), 'us_ref': deriv_us()['vix']}
    return {'us_ref': deriv_us()['vix'], 'note': '일본 파생 수급(닛케이 선물 투자자별)은 JPX 주간 자료만 무료로 공개되어 추후 추가 예정입니다.'}

# ---------------- assemble ----------------
META = {'jp': {'label': '일본 증시', 'segs': ['프라임', '스탠다드', '그로스']}, 'kr': {'label': '한국 증시', 'segs': ['KOSPI', 'KOSDAQ']},
        'us': {'label': '미국 증시', 'segs': ['Nasdaq', 'NYSE', '기타']}}
FOOT = {
 'jp': '<b>데이터 출처</b> · 시세·밸류에이션·일봉·애널리스트 추정치: Yahoo Finance · 시장구분·업종: JPX · 투자부문별 매매: JPX(주간) · 국채 금리: 재무성 · 적시공시: TDnet · 뉴스: Google 뉴스<br><b>주의</b> · 거래대금은 현재가×거래량 추정치입니다. 해설·투자 아이디어·번역은 Claude가 매일 작성하며 투자 권유가 아닙니다.',
 'kr': '<b>데이터 출처</b> · 시세·일봉·애널리스트 추정치: Yahoo Finance · PER·PBR·배당·업종·투자자별 매매: 다음 금융 · 선물·옵션 수급: 한국투자증권 Open API · 공시·대량보유: KIND · 뉴스: Google 뉴스<br><b>주의</b> · 거래대금은 현재가×거래량 추정치입니다. 해설·투자 아이디어는 Claude가 매일 작성하며 투자 권유가 아닙니다.',
 'us': '<b>데이터 출처</b> · 시세·밸류에이션·일봉·섹터·애널리스트 추정치·옵션 체인·VIX: Yahoo Finance · 8-K·13D: SEC EDGAR · 뉴스: Google 뉴스<br><b>주의</b> · 시세는 최대 15분 지연될 수 있습니다. 해설·투자 아이디어·번역은 Claude가 매일 작성하며 투자 권유가 아닙니다.',
}

def build(m, open_now):
    rows = stock_rows(m)
    if not rows: log.warning('no rows for %s', m); return None
    content = load(f'{m}.json', {}, base=CONTENT)
    strip, charts, macro, bench = market_parts(m)
    d = disc(m, rows)
    reasons = {k: [list(x) for x in v] for k, v in (content.get('reasons') or {}).items()}
    for i in d['items']:
        if i['flag'] and i['code'] and not reasons.get(i['code']): reasons[i['code']] = [['공시', i['title']]]
    newest = max([q.get('regularMarketTime') or 0 for q in load(f'{m}_raw.json', {}).values()] or [0])
    meta = {**META[m], 'asof': fmt_local(newest, m) + {'jp': ' JST', 'kr': ' KST', 'us': ' ET'}[m] if newest else '', 'asofShort': content.get('asof', ''),
            'state': '장중 · 15분마다 갱신' if open_now else '장 마감', 'live': open_now, 'built': int(time.time()), 'footer': FOOT[m]}
    D = {'meta': meta, 'cols': COLS, 'rows': rows, 'strip': strip, 'charts': charts, 'trend': load(f'{m}_trend.json', {'dates': [], 'nh': [], 'nl': [], 'ab': []}),
         'bench': bench, 'summary': content.get('summary', []), 'ideas': content.get('ideas', {'asof': '', 'items': []}), 'flows': flows(m, rows), 'rates': rates(m),
         'macro': macro, 'disc': d, 'hold': holdings(m, rows), 'news': news(m, content), 'events': content.get('events', []), 'reasons': reasons,
         'est': estimates(m, rows), 'deriv': deriv(m), 'notes': {'est': content.get('est_note', ''), 'deriv': content.get('deriv_note', '')}}
    save(f'{m}.json', D, base=OUT)
    save('meta.json', {**(load('meta.json', {}, base=OUT)), m: meta['built']}, base=OUT)
    return D

def digest(m, D):
    """Compact facts for the daily commentary (committed to the repo so a scheduled Claude task can read it)."""
    if not D: return
    I = {c: i for i, c in enumerate(COLS)}; R = D['rows']
    big = sorted([r for r in R if r[I['mcap']]], key=lambda r: -r[I['mcap']])[:400]
    f = lambda r: [r[0], r[1], r[3], r[I['chg']], r[I['r1w']], r[I['mcap']]]
    secs = {}
    for r in R:
        s = secs.setdefault(r[3], [0, 0, 0, 0]); c = r[I['mcap']] or 0; s[0] += c; s[1] += c * (r[I['chg']] or 0); s[2] += c * (r[I['r1w']] or 0); s[3] += 1
    out = {'built': D['meta']['built'], 'asof': D['meta']['asof'], 'strip': D['strip'], 'macro': D['macro'],
           'breadth': {'up': sum(1 for r in R if (r[I['chg']] or 0) > 0), 'dn': sum(1 for r in R if (r[I['chg']] or 0) < 0), 'nh': sum(r[I['nh']] for r in R), 'nl': sum(r[I['nl']] for r in R)},
           'sectors': sorted([[k, v[3], round(v[0]), round(v[1] / v[0], 2) if v[0] else None, round(v[2] / v[0], 1) if v[0] else None] for k, v in secs.items()], key=lambda x: -x[2])[:30],
           'gainers': [f(r) for r in sorted(big, key=lambda r: -(r[I['chg']] or 0))[:20]], 'losers': [f(r) for r in sorted(big, key=lambda r: (r[I['chg']] or 0))[:20]],
           'turnover': [f(r) for r in sorted(R, key=lambda r: -(r[I['to']] or 0))[:20]], 'new_highs': [f(r) for r in big if r[I['nh']]][:30],
           'flagged_disc': [[i['t'], i['code'], i['nm'], i['title'], i['orig'], i['url']] for i in D['disc']['items'] if i['flag']][:150],
           'news': [[n['t'], n['cat'], n['orig'] or n['title'], n['src'], n['url']] for n in D['news']['items']][:80],
           'flows': D['flows'], 'rates': D['rates'],
           'est_sectors': (D['est'] or {}).get('sectors'), 'deriv': {k: v for k, v in (D['deriv'] or {}).items() if k not in ('series', 'vixh')}}
    save(f'{m}.json', out, base=DIGEST, compact=False)
