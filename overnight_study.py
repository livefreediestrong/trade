"""Frozen close/open research experiments; no broker or model authority."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import re
import threading
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request
import market_catalog
import paper_loop

VERSION = 'overnight-v1'
LOCK = threading.Lock()
NOTE = ('Historical price decomposition, not account profit or an executable strategy. '
        'Adjusted opens are synthetic values on the same split/dividend basis as adjusted closes. '
        'The provider can revise history; these are current-vintage data, not information frozen in the past. '
        'Costs and filled percentages are assumptions. Auction fills, queues, cash settlement, taxes, '
        'financing, interest and whole-share sizing are not simulated. Earlier/later results are retrospective, '
        'not an untouched validation sample. No live rule or setting changes.')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def number(value):
    if isinstance(value, bool):
        raise ValueError('A number is required')
    try:
        value = float(value)
        if not math.isfinite(value) or abs(value) >= 1e100:
            raise ValueError
        return value
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('A finite, available number is required') from exc


def parameters(body, now):
    if not isinstance(body, dict) or set(body) - {'symbols', 'start', 'end', 'capital', 'cost_bps', 'fill_pct'}:
        raise ValueError('Use symbols, start/end dates, capital, cost_bps and fill_pct')
    symbols = body.get('symbols')
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 3:
        raise ValueError('Choose one to three US stock or fund symbols')
    symbols = [str(s).strip().upper() for s in symbols]
    if len(set(symbols)) != len(symbols) or any(not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}', s) for s in symbols):
        raise ValueError('Use distinct, exact US stock or fund symbols')
    try:
        start, end = date.fromisoformat(body['start']), date.fromisoformat(body['end'])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('Choose valid start and end dates') from exc
    today = now.astimezone(paper_loop.NY_TZ).date()
    if not 5 <= (end-start).days <= 730 or end > today or start < date(2025, 1, 10):
        raise ValueError('Use a past range of 5–730 days, starting January 10, 2025 or later')
    out = dict(symbols=symbols, start=start.isoformat(), end=end.isoformat())
    for key, default, lo, hi in (('capital', 1000, 1, 1e8), ('cost_bps', 5, 0, 100), ('fill_pct', 100, 1, 100)):
        out[key] = number(body.get(key, default))
        if not lo <= out[key] <= hi:
            raise ValueError(f'{key} must be between {lo} and {hi}')
    return out


def sessions(start, end, now):
    result = []
    for offset in range((end-start).days+1):
        day = start+timedelta(days=offset)
        close = session_close(day)
        if close and now >= datetime.combine(day, close, tzinfo=paper_loop.NY_TZ)+timedelta(minutes=30):
            result.append(day.isoformat())
    return result


def session_close(day):
    # NYSE/Nasdaq national day of mourning; also needed for Jan 10's baseline.
    return None if day == date(2025, 1, 9) else paper_loop.session_close_time(day)


def parse(symbol, payload, params, now):
    chart = payload.get('chart') or {}
    items = chart.get('result')
    if chart.get('error') or not isinstance(items, list) or len(items) != 1:
        raise ValueError('The provider did not return one valid history')
    data, expected = items[0], symbol.replace('.', '-')
    meta = data.get('meta') or {}
    if (meta.get('symbol') != expected or meta.get('currency') != 'USD'
            or meta.get('instrumentType') not in ('EQUITY', 'ETF')
            or meta.get('exchangeTimezoneName') != 'America/New_York'
            or meta.get('exchangeName') not in {'NMS', 'NGM', 'NCM', 'NYQ', 'ASE', 'PCX', 'BTS', 'BATS', 'NAS'}):
        raise ValueError('Exact US-listed USD stock/fund identity is unverified; no substitute used')
    start, end = date.fromisoformat(params['start']), date.fromisoformat(params['end'])
    expected_days = sessions(start, end, now)
    if not expected_days:
        raise ValueError('No completed regular sessions in this range')
    prior = date.fromisoformat(expected_days[0])-timedelta(days=1)
    while not session_close(prior):
        prior -= timedelta(days=1)
    wanted = [prior.isoformat(), *expected_days]
    stamps = data.get('timestamp')
    quotes = (data.get('indicators') or {}).get('quote') or []
    adjusted = (data.get('indicators') or {}).get('adjclose') or []
    if (not isinstance(stamps, list) or not 1 <= len(stamps) <= 800
            or len(quotes) != 1 or len(adjusted) != 1):
        raise ValueError('Dated open, close and adjusted-close history is required')
    columns = {k: quotes[0].get(k) for k in ('open', 'high', 'low', 'close')}
    columns['adjusted_close'] = adjusted[0].get('adjclose')
    if any(not isinstance(v, list) or len(v) != len(stamps) for v in columns.values()):
        raise ValueError('Price columns have inconsistent lengths')
    bars, rejected, seen = {}, [], set()
    for i, raw in enumerate(stamps):
        stamp = datetime.fromtimestamp(number(raw), timezone.utc)
        day = stamp.astimezone(paper_loop.NY_TZ).date().isoformat()
        if day not in wanted:
            continue
        if day in seen:
            raise ValueError('Duplicate session dates; history is ambiguous')
        seen.add(day)
        try:
            values = {k: number(v[i]) for k, v in columns.items()}
            if stamp > now or min(values.values()) <= 0 or not values['low'] <= min(values['open'], values['close']) <= max(values['open'], values['close']) <= values['high']:
                raise ValueError
            values['adjusted_open'] = values['open']*values['adjusted_close']/values['close']
            number(values['adjusted_open'])
            bars[day] = dict(date=day, timestamp=stamp.isoformat(), **values)
        except ValueError:
            rejected.append(day)
    missing = [day for day in wanted if day not in bars]
    events = []
    for kind in ('dividends', 'splits', 'capitalGains'):
        raw_events = (data.get('events') or {}).get(kind) or {}
        if not isinstance(raw_events, dict):
            raise ValueError('Corporate-action records are unreadable')
        for value in raw_events.values():
            event_day = datetime.fromtimestamp(number(value.get('date')), timezone.utc).astimezone(paper_loop.NY_TZ).date().isoformat()
            if wanted[0] <= event_day <= wanted[-1]:
                # Keep the provider's event fields; never add dividends twice.
                events.append(dict(kind=kind, date=event_day, provider_record=value))
    return {'symbol': symbol, 'provider_symbol': expected, 'currency': 'USD', 'exchange': meta['exchangeName'],
            'source': 'Yahoo Finance chart history', 'source_url': f'https://finance.yahoo.com/quote/{quote(expected)}/history/',
            'retrieved_at': now.isoformat(), 'payload_sha256': digest(payload),
            'bars': [bars[d] for d in wanted if d in bars], 'events': events,
            'expected_dates': wanted, 'missing_dates': missing, 'rejected_dates': rejected,
            'complete': not missing, 'action_coverage': 'Provider-returned events; completeness not independently verified',
            'calendar': 'Existing US equity-session calendar; holidays/early closes included, 30-minute completion buffer'}


def measure(factors, capital, cost_bps, fill_pct):
    cost, fill = cost_bps/10000, fill_pct/100
    returns = [(factor*(1-cost)/(1+cost)-1)*fill for factor in factors]
    equity = peak = 1.0
    drawdown = 0.0
    for change in returns:
        equity *= 1+change
        peak = max(peak, equity)
        drawdown = min(drawdown, equity/peak-1)
    return dict(samples=len(returns), mean_net_pct=sum(returns)/len(returns)*100 if returns else None,
        fixed_dollar_pnl=capital*sum(returns), reinvested_pnl=capital*(equity-1),
        compounded_pct=(equity-1)*100, worst_interval_pct=min(returns)*100 if returns else None,
        drawdown_pct=drawdown*100, positive_count=sum(r>0 for r in returns))


def analyze(observation, params):
    if not observation['complete']:
        return {'available': False, 'reason': 'Missing or invalid sessions: no gaps are bridged and no full-period result is claimed.',
                'missing_dates': observation['missing_dates']}
    bars = observation['bars']
    pairs = []
    for previous, current in zip(bars, bars[1:]):
        overnight = current['adjusted_open']/previous['adjusted_close']
        intraday = current['adjusted_close']/current['adjusted_open']
        whole = current['adjusted_close']/previous['adjusted_close']
        if not math.isclose(overnight*intraday, whole, rel_tol=1e-10):
            raise ValueError('The close/open components did not reconcile')
        pairs.append(dict(date=current['date'], previous_date=previous['date'],
            overnight_factor=overnight, intraday_factor=intraday, whole_factor=whole,
            provider_price_gap_pct=(current['open']/previous['close']-1)*100,
            adjustment_event=any(e['date']==current['date'] for e in observation['events'])))
    if not pairs:
        return {'available': False, 'reason': 'At least two completed prices are required'}
    gross = math.prod(p['overnight_factor'] for p in pairs)*math.prod(p['intraday_factor'] for p in pairs)
    endpoint = bars[-1]['adjusted_close']/bars[0]['adjusted_close']
    if not math.isfinite(gross) or not math.isclose(gross, endpoint, rel_tol=1e-9):
        raise ValueError('Full-period return reconciliation failed')
    scenarios = []
    for cost in sorted(set((0, params['cost_bps'], min(200, params['cost_bps']*2)))):
        item = {'cost_bps_per_side': cost}
        for name in ('overnight', 'intraday'):
            factors = [p[name+'_factor'] for p in pairs]
            item[name] = measure(factors, params['capital'], cost, params['fill_pct'])
        # One purchase and one sale across exactly the same endpoint dates.
        item['buy_and_hold'] = measure([endpoint], params['capital'], cost, params['fill_pct'])
        peak, drawdown = 1.0, 0.0
        for bar in bars[1:]:
            value = 1-params['fill_pct']/100 + params['fill_pct']/100*(bar['adjusted_close']/bars[0]['adjusted_close'])*(1-cost/10000)/(1+cost/10000)
            peak = max(peak,value)
            drawdown = min(drawdown,value/peak-1)
        item['buy_and_hold']['drawdown_pct'] = drawdown*100
        scenarios.append(item)
    split = max(1, min(len(pairs)-1, int(len(pairs)*.7))) if len(pairs)>1 else 1
    samples = []
    for label, part in (('Earlier 70% (retrospective)', pairs[:split]), ('Later 30% (retrospective)', pairs[split:])):
        samples.append(dict(label=label, start=part[0]['date'] if part else None, end=part[-1]['date'] if part else None,
            overnight=measure([p['overnight_factor'] for p in part],params['capital'],params['cost_bps'],params['fill_pct'])))
    strongest = sorted(range(len(pairs)), key=lambda i:pairs[i]['overnight_factor'], reverse=True)[:5]
    without_best = measure([p['overnight_factor'] for i,p in enumerate(pairs) if i not in strongest],
                           params['capital'],params['cost_bps'],params['fill_pct']) if len(pairs)>5 else None
    return dict(available=True, start=bars[0]['date'], end=bars[-1]['date'], intervals=len(pairs),
        gross_reconciled=True, gross_buy_and_hold_pct=(endpoint-1)*100, scenarios=scenarios,
        retrospective_samples=samples, without_best_five_overnights=without_best,
        best_five_dates=[pairs[i]['date'] for i in strongest], pairs=pairs,
        earnings_attribution='Unavailable: no dated earnings-event history is attached', note=NOTE)


def fetch_observation(symbol, params, now):
    start = date.fromisoformat(params['start'])-timedelta(days=10)
    end = date.fromisoformat(params['end'])+timedelta(days=1)
    period = lambda d:int(datetime.combine(d, datetime.min.time(), tzinfo=paper_loop.NY_TZ).timestamp())
    provider_symbol = symbol.replace('.', '-')
    payload = market_catalog.cached(('overnight',provider_symbol,params['start'],params['end']), 900,
        lambda: {'payload':market_catalog.fetch('v8/finance/chart/'+quote(provider_symbol,safe=''),
            {'period1':period(start),'period2':period(end),'interval':'1d','events':'div,splits,capitalGains'}),
            'retrieved_at':now.isoformat()})
    observed = parse(symbol,payload['payload'],params,datetime.fromisoformat(payload['retrieved_at']))
    observed['retrieved_at'] = payload['retrieved_at']
    return observed


def register(app, desk):
    bp = Blueprint('overnight_study', __name__)
    def path():
        return desk.DATA_DIR/'overnight_studies.json'
    def load():
        if not path().exists():
            return {'version':1,'studies':[]}
        try:
            if path().stat().st_size > 64*1024*1024:
                raise ValueError
            value = json.loads(path().read_text(encoding='utf-8'))
            if value.get('version') != 1 or not isinstance(value.get('studies'),list) or len(value['studies']) > 40:
                raise ValueError
            for item in value['studies']:
                verified(item)
            return value
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            # Optional research corruption does not contaminate the order ledger.
            raise ValueError('Saved overnight studies need recovery; they will not be overwritten') from exc
    def verified(item):
        if (not isinstance(item,dict) or not isinstance(item.get('capture'),dict)
                or not isinstance(item.get('results'),list)
                or item.get('id') != digest({'capture':item.get('capture'),'results':item.get('results')})):
            raise ValueError('Saved study evidence failed its checksum')
        if item['capture'].get('version') != VERSION:
            raise ValueError('This study needs its original calculation version')
        c = item['capture']
        if (not isinstance(c.get('parameters'),dict) or not isinstance(c.get('reads'),list)
                or not isinstance(c.get('created_at'),str)):
            raise ValueError('Saved study structure is unreadable')
        return item
    def brief(item):
        verified(item)
        c=item['capture']
        results=[]
        for result,read in zip(item['results'],c['reads']):
            observed=read.get('observation') or {}
            results.append({**{k:v for k,v in result.items() if k not in ('pairs','note')},
                'source':observed.get('source'), 'retrieved_at':observed.get('retrieved_at'),
                'adjustment_events':len(observed.get('events',[]))})
        return dict(id=item['id'],created_at=c['created_at'],parameters=c['parameters'],
                    results=results)
    @bp.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(ok=False,error=str(exc)),400
    @bp.errorhandler(OSError)
    def storage_error(exc):
        return jsonify(ok=False,error='The study could not be saved. Check local storage and reload the archive before retrying.'),503
    @bp.get('/api/research/overnight')
    def listing():
        with desk._lock:
            data=load()
        return jsonify(ok=True,studies=[brief(item) for item in reversed(data['studies'])],note=NOTE)
    @bp.post('/api/research/overnight')
    def create():
        now=datetime.now(timezone.utc)
        params=parameters(request.get_json(silent=True),now)
        if not LOCK.acquire(blocking=False):
            return jsonify(ok=False,error='An overnight study is already running'),409
        try:
            with desk._lock:
                data=load()
            if len(data['studies'])>=40:
                raise ValueError('Forty retained studies reached. Export and review the archive before adding more; nothing was deleted.')
            for existing in reversed(data['studies']):
                c=verified(existing)['capture']
                if c['parameters']==params and 0 <= (now-datetime.fromisoformat(c['created_at'])).total_seconds()<300:
                    return jsonify(ok=True,study=brief(existing),reused=True)
            def collect(symbol):
                try:
                    return {'observation':fetch_observation(symbol,params,now)}
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError, market_catalog.requests.RequestException) as exc:
                    return {'error':f'{type(exc).__name__}: source unavailable or invalid', 'symbol':symbol}
            with ThreadPoolExecutor(max_workers=3,thread_name_prefix='overnight-research') as pool:
                reads=list(pool.map(collect,params['symbols']))
            capture=dict(version=VERSION,created_at=now.isoformat(),parameters=params,reads=reads)
            results=[dict(symbol=params['symbols'][i],**analyze(r['observation'],params)) if 'observation' in r
                     else dict(symbol=r['symbol'],available=False,reason=r['error']) for i,r in enumerate(reads)]
            item=dict(id=digest(dict(capture=capture,results=results)),capture=capture,results=results)
            with desk._lock:
                data=load()
                data['studies'].append(item)
                desk._save_json(path(),data)
                if not any(s['id']==item['id'] for s in load()['studies']):
                    raise OSError('Study was not persisted')
            return jsonify(ok=True,study=brief(item),reused=False)
        finally:
            LOCK.release()
    @bp.get('/api/research/overnight/<study_id>')
    def detail(study_id):
        if not re.fullmatch('[a-f0-9]{64}',study_id):
            raise ValueError('Unknown study identifier')
        with desk._lock:
            item=next((s for s in load()['studies'] if s.get('id')==study_id),None)
        if item is None:
            return jsonify(ok=False,error='Study not found'),404
        verified(item)
        params=item['capture']['parameters']
        replay=[dict(symbol=params['symbols'][i],**analyze(r['observation'],params)) if 'observation' in r
                else dict(symbol=r['symbol'],available=False,reason=r['error']) for i,r in enumerate(item['capture']['reads'])]
        if digest(replay)!=digest(item['results']):
            raise ValueError('Stored results do not match the frozen evidence')
        return Response(json.dumps(dict(ok=True,identical=True,study=item,note=NOTE),allow_nan=False),
            mimetype='application/json',headers={'Content-Disposition':f'attachment; filename=overnight-{study_id[:12]}.json'})
    app.register_blueprint(bp)
