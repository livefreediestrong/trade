$ErrorActionPreference = "Stop"
# Replaces the desk watchlist with data\google_finance_watchlist.txt (desk must be running).
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -c @"
import json, urllib.request
from pathlib import Path
root = Path(r'$Root')
text = (root / 'data' / 'google_finance_watchlist.txt').read_text(encoding='utf-8')
body = json.dumps({'text': text, 'mode': 'replace', 'source': 'google_finance'}).encode('utf-8')
req = urllib.request.Request('http://127.0.0.1:5056/api/watchlist/import', data=body, headers={'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=60) as r:
    data = json.loads(r.read().decode())
print('ok=', data.get('ok'), 'total=', data.get('total'), 'added=', data.get('added'))
wl = data.get('watchlist') or []
print('first5=', ','.join(wl[:5]))
print('last5=', ','.join(wl[-5:]))
"@
