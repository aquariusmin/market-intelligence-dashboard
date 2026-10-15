"""Source adapters. Output: chronological {date, close} rows, never synthetic data."""
import csv
import io
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from urllib.parse import urlencode

FRED_LEGAL = 'https://fred.stlouisfed.org/legal/'
MARKETS = {
    'sp': {'name': 'S&P 500', 'symbol': 'SP500', 'provider': 'fred', 'timezone': 'America/New_York',
           'source_url': 'https://fred.stlouisfed.org/series/SP500',
           'attribution': 'S&P Dow Jones Indices LLC via FRED®',
           'copyright': 'Copyright © 2016, S&P Dow Jones Indices LLC. All rights reserved.'},
    'nq': {'name': 'NASDAQ 100', 'symbol': 'NASDAQ100', 'provider': 'fred', 'timezone': 'America/New_York',
           'source_url': 'https://fred.stlouisfed.org/series/NASDAQ100',
           'attribution': 'Nasdaq, Inc. via FRED®',
           'copyright': 'Copyright © 2016, NASDAQ OMX Group, Inc.'},
    'ks': {'name': 'KOSPI', 'symbol': 'KOSPI', 'provider': 'krx', 'timezone': 'Asia/Seoul',
           'source_url': 'https://openapi.krx.co.kr/contents/OPP/MAIN/main/index.cmd',
           'attribution': 'Korea Exchange · KRX OPEN API', 'copyright': 'Copyright KOREA Exchange. All rights reserved.'},
    'kq': {'name': 'KOSDAQ', 'symbol': 'KOSDAQ', 'provider': 'krx', 'timezone': 'Asia/Seoul',
           'source_url': 'https://openapi.krx.co.kr/contents/OPP/MAIN/main/index.cmd',
           'attribution': 'Korea Exchange · KRX OPEN API', 'copyright': 'Copyright KOREA Exchange. All rights reserved.'},
}


class RequestError(RuntimeError):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def request_text(url, headers=None, json_body=None):
    for attempt in range(3):
        try:
            return _request_text_once(url, headers, json_body)
        except RequestError as exc:
            if not exc.retryable or attempt == 2:
                raise
            print('일시적 요청 실패 · %d초 후 재시도 (%d/2)' % (attempt+1, attempt+1), file=sys.stderr)
            time.sleep(attempt+1)


def _request_text_once(url, headers=None, json_body=None):
    # curl's OS certificate store; keys travel only through stdin, never argv/logs.
    args = ['curl', '--silent', '--show-error', '--connect-timeout', '10',
            '--max-time', '30', '--max-filesize', '10000000']
    if not headers:
        args.append('--location')  # Never forward an AUTH_KEY across redirects.
    config = ''
    for key, value in (headers or {}).items():
        if any(c in value for c in '\r\n"\\'):
            raise ValueError('Invalid API key format')
        config += 'header = "' + key + ': ' + value + '"\n'
    if json_body is not None:
        args += ['--request', 'POST', '--header', 'Content-Type: application/json',
                 '--header', 'Accept: application/json', '--data-binary', json.dumps(json_body)]
    marker = '\n__MARKET_HTTP_STATUS__:'
    try:
        result = subprocess.run(args + ['--config', '-', '--write-out', marker + '%{http_code}', url],
                                input=config, text=True, capture_output=True, timeout=35)
    except subprocess.TimeoutExpired:
        raise RequestError('데이터 요청 시간 초과 · DNS/방화벽/서버 연결 확인', retryable=True) from None
    body, found, raw_status = result.stdout.rpartition(marker)
    status = raw_status.strip() if found else '000'
    if status != '000' and (not status.isdigit() or not 200 <= int(status) < 300):
        hint = {'400': '요청 형식 또는 기준일 확인',
                '401': '인증키 유효성 확인',
                '403': '인증키 승인 및 해당 API 활용 승인/접근 권한 확인',
                '404': 'API 주소 확인', '405': 'HTTP 요청 방식 확인',
                '429': '요청 한도 초과 · 잠시 후 재시도'}.get(status, '제공자 서버 응답 확인')
        raise RequestError('데이터 요청 실패 (HTTP %s, curl code %d) · %s' % (status, result.returncode, hint),
                           retryable=status in ('500', '502', '503', '504'))
    if result.returncode or status == '000':
        hint = {5: '프록시 DNS 조회 실패', 6: '서버 DNS 조회 실패',
                7: '서버 연결 실패', 28: 'DNS 또는 연결/응답 시간 초과',
                35: 'TLS 연결 실패', 56: '응답 수신 실패 · 네트워크/프록시/서버 확인',
                60: 'HTTPS 인증서 검증 실패'}.get(result.returncode, '네트워크/제공자 확인')
        raise RequestError('데이터 요청 실패 (HTTP %s, curl code %d) · %s' % (status, result.returncode, hint),
                           retryable=result.returncode in (5, 6, 7, 28, 35, 52, 56))
    return body


def fred(config, previous, today):
    start = today - timedelta(days=800)
    url = 'https://fred.stlouisfed.org/graph/fredgraph.csv?' + urlencode({
        'id': config['symbol'], 'cosd': start.isoformat(), 'coed': today.isoformat()})
    reader = csv.DictReader(io.StringIO(request_text(url)))
    if not reader.fieldnames or 'observation_date' not in reader.fieldnames or config['symbol'] not in reader.fieldnames:
        raise ValueError('FRED CSV 형식 불일치')
    rows = []
    for row in reader:
        value = row[config['symbol']]
        if value in ('', '.'):
            continue  # No interpolation or forward filling on holidays/missing observations.
        rows.append({'date': row['observation_date'], 'close': float(value)})
    return rows


def krx_day(config, day):
    key = os.environ.get('KRX_API_KEY', '').strip()
    if not key:
        raise RuntimeError('KRX_API_KEY 없음 · 같은 터미널에서 환경변수 설정 필요')
    endpoint = 'kospi_dd_trd' if config['symbol'] == 'KOSPI' else 'kosdaq_dd_trd'
    url = 'https://data-dbg.krx.co.kr/svc/apis/idx/' + endpoint
    text = request_text(url, {'AUTH_KEY': key}, json_body={'basDd': day.strftime('%Y%m%d')})
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError('KRX가 JSON 대신 다른 응답을 반환 · 인증/네트워크/서버 확인') from None
    block = payload.get('OutBlock_1') if isinstance(payload, dict) else None
    if not isinstance(block, list):
        raise ValueError('KRX OutBlock_1 없음 · 인증키 및 해당 API 활용 승인/응답 형식 확인')
    names = ('코스피', 'KOSPI') if config['symbol'] == 'KOSPI' else ('코스닥', 'KOSDAQ')
    matches = [r for r in block if str(r.get('IDX_NM', '')).strip() in names]
    if block and not matches:
        raise ValueError('KRX 응답에 대상 대표 지수 없음 · 지수명/명세 확인')
    if not matches:
        return None  # Holidays or a not-yet-published observation.
    r = matches[0]
    if r.get('BAS_DD', '').replace('/', '').replace('-', '') != day.strftime('%Y%m%d'):
        raise ValueError('KRX 기준일 불일치')
    return {'date': day.isoformat(), 'close': float(r['CLSPRC_IDX'].replace(',', ''))}


def krx(config, previous, today):
    if not os.environ.get('KRX_API_KEY', '').strip():
        raise RuntimeError('KRX_API_KEY 없음 · KRX 인증키 및 지수 API 활용 승인 필요')
    existing = {r['date']: r for r in previous}
    start = (date.fromisoformat(previous[-1]['date']) - timedelta(days=7)
             if previous else today - timedelta(days=420))
    d = start
    while d <= today:
        if d.weekday() < 5:
            try:
                row = krx_day(config, d)
            except (RuntimeError, ValueError) as exc:
                raise RuntimeError('%s 기준일 수집 실패 · %s' % (d.isoformat(), exc)) from None
            if row:
                existing[row['date']] = row
        d += timedelta(days=1)
    return sorted(existing.values(), key=lambda r: r['date'])


PROVIDERS = {'fred': fred, 'krx': krx}
