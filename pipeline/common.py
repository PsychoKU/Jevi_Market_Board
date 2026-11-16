"""Shared helpers: HTTP session with retries, JSON state, market clocks."""
import json, os, time, random, logging, datetime as dt
from zoneinfo import ZoneInfo
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, 'state')          # cached between runs (actions/cache)
SITE = os.path.join(ROOT, 'site')
OUT = os.path.join(SITE, 'data')             # published to GitHub Pages
CONTENT = os.path.join(ROOT, 'content')      # Claude-written commentary (committed)
DIGEST = os.path.join(ROOT, 'digest')        # compact daily digest for commentary (committed)
for d in (STATE, OUT, DIGEST): os.makedirs(d, exist_ok=True)

log = logging.getLogger('board')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'

def session(extra=None):
    s = requests.Session()
    s.headers.update({'User-Agent': UA, 'Accept-Language': 'ko-KR,ko;q=0.9,en;q=0.8'})
    if extra: s.headers.update(extra)
    return s

def fetch(s, method, url, tries=4, ok=(200,), **kw):
    """Request with exponential backoff on network errors, 429 and 5xx. Returns Response or None."""
    kw.setdefault('timeout', 30)
    for i in range(tries):
        try:
            r = s.request(method, url, **kw)
            if r.status_code in ok: return r
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(60, 2 ** i * 2 + random.random())); continue
            log.warning('HTTP %s %s', r.status_code, url[:120]); return None
        except requests.RequestException as e:
            log.warning('net error %s %s', e.__class__.__name__, url[:120]); time.sleep(2 ** i + random.random())
    return None

def jget(s, url, **kw):
    r = fetch(s, 'GET', url, **kw)
    try: return r.json() if r is not None else None
    except ValueError: return None

def load(name, default=None, base=STATE):
    p = os.path.join(base, name)
    try:
        with open(p, encoding='utf-8') as f: return json.load(f)
    except (OSError, ValueError): return default

def save(name, obj, base=STATE, compact=True):
    p = os.path.join(base, name); os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':') if compact else None, indent=None if compact else 1)
    os.replace(tmp, p)

TZ = {'kr': ZoneInfo('Asia/Seoul'), 'jp': ZoneInfo('Asia/Tokyo'), 'us': ZoneInfo('America/New_York')}
SESSION = {'kr': ((9, 0), (15, 30)), 'jp': ((9, 0), (15, 30)), 'us': ((9, 30), (16, 0))}

def now_utc(): return dt.datetime.now(dt.timezone.utc)

def local_now(m): return now_utc().astimezone(TZ[m])

def is_weekday(m): return local_now(m).weekday() < 5

def in_window(m, pre_min=20, post_min=25):
    """True while the market is open, plus a little before/after (pre-market quotes and closing prints)."""
    if not is_weekday(m): return False
    t = local_now(m); (oh, om), (ch, cm) = SESSION[m]
    o = t.replace(hour=oh, minute=om, second=0, microsecond=0) - dt.timedelta(minutes=pre_min)
    c = t.replace(hour=ch, minute=cm, second=0, microsecond=0) + dt.timedelta(minutes=post_min)
    return o <= t <= c

def after_close(m, minutes=30):
    t = local_now(m); (ch, cm) = SESSION[m][1]
    return is_weekday(m) and t >= t.replace(hour=ch, minute=cm, second=0, microsecond=0) + dt.timedelta(minutes=minutes)

def market_date(m):
    """The trading date whose data is current: today after the open, else the previous weekday."""
    t = local_now(m); d = t.date()
    (oh, om) = SESSION[m][0]
    if t.weekday() >= 5 or t < t.replace(hour=oh, minute=om) - dt.timedelta(minutes=20):
        d -= dt.timedelta(days=1)
        while d.weekday() >= 5: d -= dt.timedelta(days=1)
    return d.isoformat()

def r2(x, d=2):
    try:
        if x is None or x != x or x in (float('inf'), float('-inf')): return None
        return round(float(x), d)
    except (TypeError, ValueError): return None
