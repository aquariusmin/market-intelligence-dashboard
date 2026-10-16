#!/usr/bin/env python3
"""Manual macro snapshots for local reference only, isolated from future ML datasets."""
import argparse
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sys
from urllib.parse import quote
from collect_data import atomic_json
from providers import fred, request_text


def fred_config(name, symbol, unit, source, minimum=-100, maximum=100000):
    return dict(name=name, symbol=symbol, provider='fred', unit=unit,
                change_unit='%p' if unit=='%' else ('pt' if unit=='2006=100' else unit), frequency='daily',
                source=source+' via FRED®', source_url='https://fred.stlouisfed.org/series/'+symbol,
                terms_url='https://fred.stlouisfed.org/legal/', minimum=minimum, maximum=maximum,
                usage={'scope':'local_reference_only', 'ml_training_allowed':False,
                       'reason':'FRED AI/ML restriction; original provider rights also apply'},
                copyright=('Copyright © Cboe. All rights reserved.' if symbol=='VIXCLS' else
                           'Copyright © ICE Data Indices, LLC. All rights reserved.' if symbol=='BAMLH0A0HYM2' else
                           'Source: Board of Governors of the Federal Reserve System (US) / Federal Reserve Bank of St. Louis.'))


MACRO = {
    'vix':fred_config('VIX','VIXCLS','pt','Cboe',0,200),
    'us2y':fred_config('미국 국채 2년','DGS2','%','Federal Reserve Board'),
    'us10y':fred_config('미국 국채 10년','DGS10','%','Federal Reserve Board'),
    'yield_spread':fred_config('미국 10Y − 2Y','T10Y2Y','%p','Federal Reserve Bank of St. Louis'),
    'hy_spread':fred_config('미국 High Yield OAS','BAMLH0A0HYM2','%p','ICE Data Indices, LLC',0,100),
    'dxy':dict(name='Dollar Index · ICE DXY',provider='unavailable',unit='pt',frequency='daily',
               source='ICE',source_url='https://www.ice.com/fx-index', terms_url='https://www.ice.com/market-data',
               usage={'scope':'local_reference_only','ml_training_allowed':False,'reason':'License not secured'}),
    'broad_dollar':fred_config('연준 광의 달러 지수 · DXY 아님','DTWEXBGS','2006=100','Federal Reserve Board',0),
    'usdkrw':fred_config('USD/KRW · 뉴욕 정오 기준','DEXKOUS','KRW/USD','Federal Reserve Board',0),
}
for key,name,table,item in [('bok_rate','한국은행 기준금리','722Y001','0101000'),
                             ('kr3y','한국 국고채 3년','817Y002','010200000')]:
    MACRO[key]=dict(name=name,provider='ecos',symbol=table+'/'+item,table=table,item=item,
        unit='%',change_unit='%p',frequency='daily',source='한국은행 ECOS',source_url='https://ecos.bok.or.kr/',
        terms_url='https://ecos.bok.or.kr/',minimum=-100,maximum=100,
        copyright='한국은행 ECOS · 원 통계 작성기관의 저작권 및 이용지침 적용',
        usage={'scope':'local_reference_only','ml_training_allowed':False,
               'reason':'Training rights not reviewed; retain in reference-only store'})


def ecos(config, previous, today):
    key=os.environ.get('ECOS_API_KEY','').strip()
    if not key:
        raise RuntimeError('ECOS_API_KEY 없음 · 한국은행 ECOS 무료 인증키 필요 (KRX 키와 별개)')
    start=(today-timedelta(days=800)).strftime('%Y%m%d')
    url=('https://ecos.bok.or.kr/api/StatisticSearch/'+quote(key,safe='')+
         '/json/kr/1/1000/'+config['table']+'/D/'+start+'/'+today.strftime('%Y%m%d')+'/'+config['item'])
    # Key-bearing URL is put in curl stdin config by request_text; never stdout/argv.
    try:
        payload=json.loads(request_text(url,{'Accept':'application/json'}))
        block=payload.get('StatisticSearch',{})
        rows=block.get('row')
        if not isinstance(rows,list):
            raise ValueError('ECOS 통계 응답 없음 · 키/통계표/항목/주기 확인')
        if int(block.get('list_total_count',len(rows)))>1000:
            raise ValueError('ECOS 결과 잘림 · 조회 범위 축소 필요')
        out=[]
        for r in rows:
            if r.get('STAT_CODE')!=config['table'] or r.get('ITEM_CODE1')!=config['item']:
                raise ValueError('ECOS 통계 식별자 불일치')
            t=r['TIME']
            d=datetime.strptime(t,'%Y%m%d').date().isoformat()
            if r.get('DATA_VALUE') in ('',None,'-'): continue
            out.append({'date':d,'close':float(r['DATA_VALUE'].replace(',',''))})
        return out
    except json.JSONDecodeError:
        raise ValueError('ECOS JSON 응답 오류') from None


def validate_macro_rows(rows, config, today):
    if not isinstance(rows,list) or not rows: raise ValueError('관측값 없음')
    previous=''
    for r in rows:
        d=date.fromisoformat(r['date']); v=r['close']
        if r['date']<=previous or d>today or isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):
            raise ValueError('관측일 중복/역행/미래일 또는 비정상 값')
        if not config.get('minimum',-100000)<=v<=config.get('maximum',100000):
            raise ValueError('지표 허용 범위 초과')
        previous=r['date']
    return rows


def collect_macro(output, adapters=None, today=None, providers=None):
    adapters=adapters if adapters is not None else {'fred':fred,'ecos':ecos}
    today=today or date.today()
    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    old={'schema_version':1,'indicators':{}}
    if output.exists():
        old=json.loads(output.read_text(encoding='utf-8'))
        if old.get('schema_version')!=1 or not isinstance(old.get('indicators'),dict):
            raise ValueError('기존 거시 JSON 스키마 오류 · 원본 보존')
    result={'schema_version':1,'usage_scope':'local_reference_only',
            'ml_training_allowed':False,'last_attempt_at':now,'last_success_at':old.get('last_success_at'),
            'indicators':{}}
    failures=successes=0
    for key,config in MACRO.items():
        prev=old['indicators'].get(key,{})
        if providers and config['provider'] not in providers:
            # not requested in this run: keep whatever the file already had (never fetched, never stored anew)
            result['indicators'][key]=dict(prev) if prev else dict(config,value=None,observation_date=None,
                publication_date=None,source_updated_at=None,updated_at=None,history=[],change=None,
                previous_observation_date=None,status='unavailable',last_attempt_at=None,
                error='이 실행에서 수집하지 않음 (--providers %s)'%','.join(providers))
            continue
        try:
            if config['provider']=='unavailable':
                raise RuntimeError('무료 수집원/이용 권한 미확보 · 광의 달러 지수를 DXY로 대체하지 않음')
            rows=validate_macro_rows(adapters[config['provider']](config,prev.get('history',[]),today),config,today)
            last=rows[-1]
            if prev.get('observation_date') and last['date']<prev['observation_date']:
                raise ValueError('이전 정상 관측일보다 과거인 응답')
            if (today-date.fromisoformat(last['date'])).days>14:
                raise ValueError('최근 관측값 14일 초과 지연 · 기존 데이터 유지')
            if prev.get('history') and len(rows)<min(100,len(prev['history'])):
                raise ValueError('이력 급감 · 기존 데이터 유지')
            entry=dict(config,value=last['close'],observation_date=last['date'],
                # CSV/daily ECOS rows do not provide authoritative release timestamps.
                publication_date=None,source_updated_at=None,updated_at=now,last_attempt_at=now,
                status='ok',error=None,history=rows,
                previous_observation_date=rows[-2]['date'] if len(rows)>1 else None,
                change=last['close']-rows[-2]['close'] if len(rows)>1 else None)
            successes+=1
            print('%s: OK · %s · %s %s'%(key,last['date'],last['close'],config['unit']))
        except Exception as exc:
            failures+=1
            entry=dict(prev) if prev.get('history') else dict(config,value=None,observation_date=None,
                publication_date=None,source_updated_at=None,updated_at=None,history=[],change=None,
                previous_observation_date=None)
            entry.update(status='stale' if prev.get('history') else 'unavailable',error=str(exc),last_attempt_at=now)
            print('%s: %s · %s'%(key,entry['status'],exc),file=sys.stderr)
        result['indicators'][key]=entry
    if successes: result['last_success_at']=now
    atomic_json(output,result)
    return failures


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'data/reference_only/macro.json')
    parser.add_argument('--providers',nargs='+',choices=['fred','ecos'],
                        help='collect only these providers; other entries keep their previous values (default: all)')
    args=parser.parse_args()
    try: sys.exit(1 if collect_macro(args.output,providers=args.providers) else 0)
    except Exception as exc:
        print('Macro collection aborted; snapshot unchanged: %s'%exc,file=sys.stderr)
        sys.exit(2)
