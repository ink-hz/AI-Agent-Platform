#!/usr/bin/env python3
"""Generate pinned schema assets; optionally copy the same release into MetaBot."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.execution_relay.contracts_v6 import CONTRACT_VERSION, contract_schemas


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--metabot', type=Path)
    args = parser.parse_args()
    assets = {name: (json.dumps(body, ensure_ascii=False, indent=2) + '\n').encode()
              for name, body in contract_schemas().items()}
    manifest = {'contractVersion': CONTRACT_VERSION, 'authority': 'AI-Agent-Platform',
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in assets.items()}}
    assets['manifest.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    targets = [ROOT / 'contracts/hr-execution/v6']
    if args.metabot:
        targets.append(args.metabot / 'src/runtime/contracts/hr-execution-v6')
    for target in targets:
        target.mkdir(parents=True, exist_ok=True)
        for name, data in assets.items():
            (target / name).write_bytes(data)
    print('Published identical HR v6 assets to', len(targets), 'checkout(s)')


if __name__ == '__main__':
    main()
