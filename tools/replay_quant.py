"""Replay explicit, recorded JSONL ticks into a NEW isolated paper database."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant_models import Tick, RiskConfig
from quant_risk import RiskManager, RiskStore
from quant_engine import PaperEngine, PaperGateway, MomentumAlpha


async def replay(source: Path, database: Path, starting_cash: Decimal) -> dict:
    # Refuse reusing a production or previously halted book. No automatic halt reset.
    database.parent.mkdir(parents=True, exist_ok=True)
    with database.open('xb'):
        pass
    store = RiskStore(database, 'isolated-replay', starting_cash)
    clock = [datetime.now(timezone.utc)]
    monotonic = [0.]
    risk = RiskManager(RiskConfig(), store, monotonic=lambda:monotonic[0])
    async def records():
        previous = None
        with source.open(encoding='utf-8') as stream:
            for line_no, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                tick = Tick.model_validate_json(line)
                if tick.data_kind != 'recorded':
                    raise ValueError(f'Line {line_no}: replay accepts recorded data only')
                if previous and tick.received_at < previous:
                    raise ValueError(f'Line {line_no}: receipt timestamps go backward')
                if previous:
                    monotonic[0] += (tick.received_at - previous).total_seconds()
                previous = clock[0] = tick.received_at
                yield tick
                await asyncio.sleep(0)
    try:
        await PaperEngine(risk, MomentumAlpha(), PaperGateway(risk), now=lambda:clock[0]).run(records())
        return {'environment':'isolated recorded-data paper replay', 'database':str(database.resolve()),
            'cash':store.get('cash'), 'positions':{k:str(v) for k,v in store.positions().items()},
            'orders':store.db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],
            'fills':store.db.execute('SELECT COUNT(*) FROM fills').fetchone()[0], 'halt':risk.halted}
    finally:
        store.close()


if __name__ == '__main__':
    import json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticks',type=Path,required=True,help='Recorded Tick JSONL; never a broker connection')
    parser.add_argument('--database',type=Path,required=True,help='A new file for this isolated replay')
    parser.add_argument('--cash',type=Decimal,default=Decimal('1000'))
    args=parser.parse_args()
    print(json.dumps(asyncio.run(replay(args.ticks,args.database,args.cash)),indent=2))
