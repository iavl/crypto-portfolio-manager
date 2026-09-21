#!/usr/bin/env python3
"""Validate a frozen review bundle and emit authoritative final report inputs."""
import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crypto_portfolio.state.review import finalize_review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', help='JSON containing decision, snapshot and finalized acquisition')
    parser.add_argument('--artifact-root', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--persist', action='store_true')
    parser.add_argument('--decision-path')
    args = parser.parse_args()
    value = json.loads(Path(args.bundle).read_text())
    result = finalize_review(value['decision'], value['snapshot'], acquisition=value['acquisition'],
        artifact_root=args.artifact_root, history=value.get('history', ()), new_cash=value.get('new_cash', 0),
        persist=args.persist, decision_path=args.decision_path)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    print(result['operation']['decision'])


if __name__ == '__main__':
    main()
