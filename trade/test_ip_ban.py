#!/usr/bin/env python3
import os, json, datetime, tempfile, shutil
import ip_ban as b

SAIGON = datetime.timezone(datetime.timedelta(hours=7))
MS = 1794535678732  # 2026-11-13 09:07:58+07

def test_parse_ms():
    until = b.parse_until(f'Way too many requests; IP(1.2.3.4) banned until {MS}. Please use the websocket')
    assert until is not None
    assert until.strftime('%Y-%m-%dT%H:%M:%S+07:00') == '2026-11-13T09:07:58+07:00', until

def test_parse_seconds():
    until = b.parse_until('banned until 1794535678')
    assert until.strftime('%Y-%m-%dT%H:%M:%S+07:00') == '2026-11-13T09:07:58+07:00'

def test_is_ban():
    assert b.is_ban({'http': 418, 'err': 'x'})
    assert b.is_ban({'code': -1003, 'msg': 'Way too many requests'})
    assert b.is_ban('banned until 1')
    assert not b.is_ban({'code': -2019})
    assert not b.is_ban({'walletBalance': '1'})

def test_roundtrip(tmp):
    b.FILE = os.path.join(tmp, 'ip_ban.json')
    until = b.parse_until(f'banned until {MS}')
    now = datetime.datetime(2026, 11, 13, 8, 59, tzinfo=SAIGON)
    b.save(until, raw='test', telegram_sent=True, now=now)
    active = b.load_active(now=now)
    assert active is not None
    assert active['telegram_sent'] is True
    assert active['until'] == until
    later = datetime.datetime(2026, 11, 13, 9, 8, tzinfo=SAIGON)
    assert b.load_active(now=later) is None

if __name__ == '__main__':
    test_parse_ms()
    test_parse_seconds()
    test_is_ban()
    tmp = tempfile.mkdtemp()
    try:
        test_roundtrip(tmp)
    finally:
        shutil.rmtree(tmp)
    print('ok')
