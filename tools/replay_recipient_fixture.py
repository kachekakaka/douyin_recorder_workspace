from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.douyin.recipient import RecipientContract  # noqa: E402
from app.douyin.replay import render_markdown, run_fixture  # noqa: E402

DEFAULT_FIXTURE = ROOT / "tests" / "replay" / "fixtures" / "recipient-strict-unknown.synthetic.json"
DEFAULT_CONTRACT = ROOT / "tests" / "replay" / "contracts" / "recipient.synthetic-v1.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="回放严格推荐收礼人合成 fixture")
    parser.add_argument("fixture", nargs="?", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--quiet", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    contract = RecipientContract.load(args.contract)
    result = run_fixture(args.fixture, contract=contract)
    payload = result.to_dict()
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if not args.quiet:
        print(text, end="")
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(text, encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(render_markdown(result, contract), encoding="utf-8")
    if result.fixture_live_verified or contract.live_verified:
        raise SystemExit("合成 P0 fixture/contract 不得标记为 live_verified=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
