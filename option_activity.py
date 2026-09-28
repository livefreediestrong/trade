"""Local, owner-supplied option activity evidence. Never an execution/risk input."""
import copy
import csv
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import re
import time
import uuid

from flask import jsonify, request

import option_lifecycle as life

MAX_BYTES, MAX_ROWS, MAX_EVENTS = 1_000_000, 3000, 30000
MONEY = ('trade_price', 'proceeds', 'commission_tax', 'basis', 'realized_pnl')
ALIASES = {
    'accountid': 'account', 'clientaccountid': 'account',
    'assetclass': 'asset', 'assetcategory': 'asset', 'currency': 'currency',
    'conid': 'con_id', 'symbol': 'symbol', 'underlyingsymbol': 'underlying',
    'date': 'date', 'tradedate': 'date', 'transactiontype': 'type',
    'quantity': 'quantity', 'tradeid': 'trade_id', 'tradeprice': 'trade_price',
    'proceeds': 'proceeds', 'commtax': 'commission_tax', 'basis': 'basis',
    'realizedpl': 'realized_pnl', 'fifopnlrealized': 'realized_pnl',
    'levelofdetail': 'detail', 'model': 'model',
}
REQUIRED = {'account', 'asset', 'currency', 'con_id', 'symbol', 'date', 'type', 'quantity', 'trade_id'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def numeric(text, *, whole=False, optional=False):
    if not text and optional:
        return None
    if not isinstance(text, str) or not re.fullmatch(r'[+-]?(?:\d{1,16})(?:\.\d{1,8})?', text):
        raise ValueError('Invalid number; export plain numbers without currency symbols or grouping separators')
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValueError('Invalid numeric amount') from None
    if abs(value) > Decimal('1000000000000000') or (whole and value != value.to_integral_value()):
        raise ValueError('Amount is outside supported bounds or contracts are not whole')
    return '0' if value == 0 else format(value.normalize(), 'f')


def parse(text, owner):
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_BYTES or '\x00' in text:
        raise ValueError('Choose a UTF-8 CSV report no larger than 1 MB')
    try:
        reader = csv.reader(io.StringIO(text.lstrip('\ufeff')), strict=True)
        header = next(reader)
        keys = [ALIASES.get(re.sub('[^a-z0-9]', '', h.lower().strip())) for h in header]
        named = [k for k in keys if k]
        if not REQUIRED <= set(named) or len(named) != len(set(named)):
            raise ValueError('Use a single-section detailed Flex CSV with Account ID, Asset Class, Currency, Conid, Symbol, Date, Transaction Type, Quantity and Trade ID; duplicate columns are not supported')
        records = {}
        for line, values in enumerate(reader, 2):
            if not values or not any(x.strip() for x in values):
                continue
            if line > MAX_ROWS + 1 or len(values) != len(header):
                raise ValueError('Too many rows or a row has a different number of columns')
            row = {k: v.strip() for k, v in zip(keys, values) if k}
            if row['account'] != owner['account_id']:
                raise ValueError(f'Row {line}: account does not match the selected desk account; export one account with its Account ID, not its alias')
            if row.get('model') or row.get('detail', '').upper() not in ('', 'DETAIL'):
                raise ValueError(f'Row {line}: model allocations and summary/lot rows are unsupported; export detailed account-level activity')
            asset = row['asset'].upper()
            kind = row['type'].lower()
            if (asset == 'OPT' and kind not in ('assignment', 'exercise', 'expiration')) or (asset == 'STK' and kind not in ('buy', 'sell')) or asset not in ('OPT', 'STK'):
                raise ValueError(f'Row {line}: unsupported event or instrument; choose only the Options, Exercises, Assignments and Expirations section')
            if not re.fullmatch('[A-Z]{3}', row['currency']) or not re.fullmatch(r'\d{1,16}', row['con_id']) or int(row['con_id']) <= 0:
                raise ValueError(f'Row {line}: currency or contract ID is invalid')
            if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', row['trade_id']):
                raise ValueError(f'Row {line}: a stable Trade ID is required')
            if any(not row.get(k) or len(row[k]) > 100 or any(ord(c) < 32 for c in row[k]) for k in ('symbol',)):
                raise ValueError(f'Row {line}: symbol is missing or invalid')
            if len(row.get('underlying', '')) > 100 or any(ord(c) < 32 for c in row.get('underlying', '')):
                raise ValueError(f'Row {line}: underlying symbol is invalid')
            if not re.fullmatch(r'\d{8}|\d{4}-\d{2}-\d{2}', row['date']):
                raise ValueError(f'Row {line}: export dates as YYYY-MM-DD or YYYYMMDD')
            day = date.fromisoformat(row['date'])
            if day > life.now_utc().astimezone(life.paper_loop.NY_TZ).date():
                raise ValueError(f'Row {line}: activity date is in the future')
            event = dict(account_scope=copy.deepcopy(owner), asset=asset, currency=row['currency'],
                         con_id=int(row['con_id']), symbol=row['symbol'], underlying=row.get('underlying') or None,
                         date=day.isoformat(), type=kind, trade_id=row['trade_id'],
                         quantity=numeric(row['quantity'], whole=asset == 'OPT'))
            if Decimal(event['quantity']) == 0:
                raise ValueError(f'Row {line}: zero-quantity or cancellation/audit rows need separate review')
            event.update({k: numeric(row.get(k, ''), optional=True) for k in MONEY})
            key = digest([owner, event['trade_id'], event['asset'], event['con_id']])
            version = digest(event)
            records[version] = dict(key=key, event=event)
        if not records:
            raise ValueError('The report has no supported detailed activity rows')
        return records
    except (csv.Error, StopIteration) as exc:
        raise ValueError('The file is not a supported detailed CSV report') from exc


def selected(desk):
    owner = life.scope(desk.load_config().get('broker_identity'))
    if owner.get('broker') != 'ibkr':
        raise ValueError('Select a verified IBKR account in the desk before reviewing its report')
    return owner


def load(desk):
    path = desk.DATA_DIR / 'option_activity_evidence.json'
    # Optional report evidence must fail its own view, not poison execution's
    # global corrupt-state gate or be silently replaced by an empty import.
    try:
        if path.stat().st_size > 100_000_000:
            raise ValueError('Stored option activity exceeds its review limit')
        raw = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        raw = dict(version=1, events={}, reports=[])
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError('Stored option activity is unreadable; restore it before importing') from None
    try:
        if str(path.resolve()) in desk._CORRUPT_PATHS or raw['version'] != 1 or not isinstance(raw['events'], dict) or not isinstance(raw['reports'], list):
            raise ValueError('Stored option activity is unreadable; restore it before importing')
        if len(raw['events']) > MAX_EVENTS or len(raw['reports']) > 500:
            raise ValueError('Stored option activity exceeds its review limit')
        for version, record in raw['events'].items():
            event = record['event']
            if version != digest(event) or record['key'] != digest([event['account_scope'], event['trade_id'], event['asset'], event['con_id']]):
                raise ValueError('Stored option activity evidence has changed')
        return raw
    except (KeyError, TypeError):
        raise ValueError('Stored option activity is unreadable; restore it before importing') from None


def matching(event, positions):
    if event['asset'] != 'OPT':
        return []
    matches = []
    for row in positions.values():
        opened = datetime.fromisoformat(row['opened_at']).astimezone(life.paper_loop.NY_TZ).date()
        if (row['account_scope'] == event['account_scope'] and date.fromisoformat(event['date']) >= opened
                and any(leg['con_id'] == event['con_id'] for leg in row['legs'])):
            matches.append(row['id'])
    return matches


def summarize(raw, owner, positions):
    groups = {}
    for record in raw['events'].values():
        if record['event']['account_scope'] == owner:
            groups.setdefault(record['key'], []).append(record['event'])
    totals, rows = {}, []
    for key, variants in groups.items():
        conflict = len(variants) > 1
        for event in variants:
            matches = matching(event, positions)
            public = {k: v for k, v in event.items() if k != 'account_scope'}
            public.update(conflict=conflict, match_count=len(matches),
                          match='Contract matches one tracked position; holdings still need reconciliation' if len(matches) == 1 else 'Multiple tracked records; matching is ambiguous' if matches else 'No unique tracked option match')
            rows.append(public)
            if conflict:
                continue
            currency = totals.setdefault(event['currency'], dict(rows=0, realized_pnl=Decimal(0), pnl_missing=0))
            currency['rows'] += 1
            if event['realized_pnl'] is None:
                currency['pnl_missing'] += 1
            else:
                currency['realized_pnl'] += Decimal(event['realized_pnl'])
    for group in totals.values():
        group['realized_pnl'] = None if group['pnl_missing'] == group['rows'] else format(group['realized_pnl'], 'f')
    rows.sort(key=lambda r: (r['date'], r['trade_id']), reverse=True)
    return dict(account_suffix=owner['account_id'][-4:], paper_mode=owner['paper_mode'],
                total_rows=len(rows), conflicting_events=sum(len(v) > 1 for v in groups.values()),
                by_currency=totals, rows=rows[:100], truncated=len(rows) > 100,
                note='Owner-supplied statement evidence, not independently authenticated. Reported P&L covers imported rows only; missing amounts stay unknown and conflicting versions are excluded. No extra fee subtraction. This does not change holdings, live P&L, risk checks, review holds or trading controls.')


def register(bp, desk):
    tokens = {}

    def positions():
        return desk._live_agent.load().get('option_positions', {})

    @bp.get('/api/live-agent/options/activity', endpoint='option_activity_status')
    def status():
        with desk._lock:
            return jsonify(ok=True, **summarize(load(desk), selected(desk), positions()))

    @bp.post('/api/live-agent/options/activity/preview', endpoint='option_activity_preview')
    def preview():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {'csv', 'filename'} or not isinstance(body['filename'], str):
            raise ValueError('Choose one local CSV file to review')
        filename = body['filename'].replace('\\', '/').rsplit('/', 1)[-1]
        if not 1 <= len(filename) <= 120 or not filename.lower().endswith('.csv') or any(ord(c) < 32 for c in filename):
            raise ValueError('Use a CSV filename of at most 120 characters')
        with desk._lock:
            owner = selected(desk)
            before = load(desk)
            records = parse(body['csv'], owner)
            proposed = copy.deepcopy(before)
            added = len(set(records) - set(before['events']))
            proposed['events'].update(records)
            report_sha = hashlib.sha256(body['csv'].encode('utf-8')).hexdigest()
            report = dict(sha256=report_sha, filename=filename, account_scope=owner,
                          imported_at=life.now_utc().isoformat(), rows=len(records))
            if not any(r['sha256'] == report_sha and r['account_scope'] == owner for r in proposed['reports']):
                proposed['reports'].append(report)
            if len(proposed['events']) > MAX_EVENTS or len(proposed['reports']) > 500:
                raise ValueError('Local evidence store is full; archive it through a reviewed maintenance step before adding more')
            now = time.monotonic()
            for token in list(tokens):
                if tokens[token]['until'] <= now:
                    tokens.pop(token)
            while len(tokens) >= 5:
                tokens.pop(next(iter(tokens)))
            token = uuid.uuid4().hex
            # Tokens retain at most one bounded upload, not five whole-store copies.
            tokens[token] = dict(owner=owner, before=digest(before), records=records, report=report, until=now+120)
            result = summarize(proposed, owner, positions())
        return jsonify(ok=True, token=token, added=added, already_stored=len(records)-added,
                       expires_at=(life.now_utc()+timedelta(seconds=120)).isoformat(), **result)

    @bp.post('/api/live-agent/options/activity/save', endpoint='option_activity_save')
    def save():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {'token', 'acknowledge'} or not isinstance(body['token'], str) or body['acknowledge'] is not True:
            raise ValueError('Review the report and acknowledge its limits before saving')
        with desk._lock:
            saved = tokens.pop(body['token'], None)
            if not saved or saved['until'] <= time.monotonic():
                raise ValueError('Preview expired or was already used; read the file again')
            raw = load(desk)
            if selected(desk) != saved['owner'] or digest(raw) != saved['before']:
                raise ValueError('Selected account or stored evidence changed; read the file again')
            raw['events'].update(saved['records'])
            report = saved['report']
            if not any(r['sha256'] == report['sha256'] and r['account_scope'] == saved['owner'] for r in raw['reports']):
                raw['reports'].append(report)
            result = summarize(raw, saved['owner'], positions())
            desk._save_json(desk.DATA_DIR/'option_activity_evidence.json', raw)
            if digest(load(desk)) != digest(raw):
                raise ValueError('Statement save could not be confirmed; refresh saved evidence before retrying')
        return jsonify(ok=True, message='Statement evidence saved locally. Trading controls and review holds are unchanged.', **result)
