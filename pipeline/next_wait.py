"""Seconds to wait before the next run: 15 minutes after this run started while a market is active,
otherwise until shortly before the next session (or the after-close full refresh). Capped so a job stays under 6 hours."""
import sys, datetime as dt
from common import TZ, SESSION

def active(t):
    for m in ('us', 'kr', 'jp'):
        lt = t.astimezone(TZ[m])
        if lt.weekday() >= 5: continue
        (oh, om), (ch, cm) = SESSION[m]
        o = lt.replace(hour=oh, minute=om, second=0, microsecond=0) - dt.timedelta(minutes=20)
        c = lt.replace(hour=ch, minute=cm, second=0, microsecond=0) + dt.timedelta(minutes=50)   # includes the after-close full run
        if o <= lt <= c: return True
    return False

def wait(started, now, cap=5 * 3600):
    t = started + dt.timedelta(minutes=15)
    while t - now < dt.timedelta(seconds=cap):
        if t >= now and active(t): return int((t - now).total_seconds())
        t += dt.timedelta(minutes=5)
    return cap

if __name__ == '__main__':
    started = dt.datetime.fromisoformat(sys.argv[1].replace('Z', '+00:00'))
    print(wait(started, dt.datetime.now(dt.timezone.utc)))
