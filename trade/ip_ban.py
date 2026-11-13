"""Persist Binance IP bans (418 / -1003) so later runs do not hit the API until expiry."""
import json, os, re, datetime

SAIGON = datetime.timezone(datetime.timedelta(hours=7))
FILE = '/workspace/trade/ip_ban.json'
# If the 418 body has no timestamp, stay dark this long.
FALLBACK_MIN = 15

def parse_until(blob, now=None):
    """Return timezone-aware Saigon datetime, or None if not found."""
    m = re.search(r'banned until\s+(\d+)', str(blob), re.I)
    if not m:
        return None
    n = int(m.group(1))
    if n > 10_000_000_000:  # milliseconds
        n = n / 1000.0
    return datetime.datetime.fromtimestamp(n, SAIGON)

def is_ban(r):
    if r is None:
        return False
    blob = str(r)
    if isinstance(r, dict):
        if r.get('http') == 418 or r.get('code') == -1003:
            return True
    return 'banned until' in blob.lower() or 'way too many requests' in blob.lower() or 'too many requests' in blob.lower()

def load_active(now=None):
    now = now or datetime.datetime.now(SAIGON)
    try:
        d = json.load(open(FILE))
    except Exception:
        return None
    until = None
    if d.get('until_ms'):
        until = datetime.datetime.fromtimestamp(int(d['until_ms']) / 1000.0, SAIGON)
    elif d.get('until_saigon'):
        try:
            until = datetime.datetime.fromisoformat(d['until_saigon'])
            if until.tzinfo is None:
                until = until.replace(tzinfo=SAIGON)
        except Exception:
            until = None
    if until is None or until <= now:
        return None
    d['until'] = until
    return d

def save(until, raw='', telegram_sent=False, now=None):
    now = now or datetime.datetime.now(SAIGON)
    if until is None:
        until = now + datetime.timedelta(minutes=FALLBACK_MIN)
    data = {
        'until_ms': int(until.timestamp() * 1000),
        'until_saigon': until.strftime('%Y-%m-%dT%H:%M:%S+07:00'),
        'saved': now.strftime('%Y-%m-%dT%H:%M:%S+07:00'),
        'raw': str(raw)[:500],
        'telegram_sent': bool(telegram_sent),
    }
    tmp = FILE + '.tmp'
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    with open(tmp, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, FILE)
    return data

def mark(r, now=None):
    """Record a ban from an API response. Returns the until datetime."""
    now = now or datetime.datetime.now(SAIGON)
    until = parse_until(r, now=now)
    prev = load_active(now=now)
    # Keep the later expiry if we already have one.
    if prev and prev['until'] and (until is None or prev['until'] > until):
        until = prev['until']
        sent = bool(prev.get('telegram_sent'))
    else:
        sent = bool(prev.get('telegram_sent')) if prev else False
    save(until, raw=r, telegram_sent=sent, now=now)
    return until or (now + datetime.timedelta(minutes=FALLBACK_MIN))

def mark_telegram_sent():
    try:
        d = json.load(open(FILE))
    except Exception:
        return
    d['telegram_sent'] = True
    tmp = FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, FILE)

def format_until(until):
    if not until:
        return 'chua ro'
    return until.strftime('%H:%M %d/%m') + ' gio VN'
