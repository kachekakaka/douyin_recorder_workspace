from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.douyin.evidence import EvidenceError, export_approved_fixture  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="在人工 approval/hash 门禁通过后导出去标识 recipient candidate fixture"
    )
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--allow-private-output", action="store_true")
    parser.add_argument(
        "--contract",
        type=Path,
        default=ROOT / "app" / "douyin" / "contracts" / "provisional_v1.json",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        fixture = export_approved_fixture(
            evidence_dir=args.evidence,
            approval_path=args.approval,
            contract_path=args.contract,
            output_path=args.output,
            fixture_name=args.name,
            repository_root=ROOT,
            allow_private_output=args.allow_private_output,
        )
    except EvidenceError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(fixture, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
