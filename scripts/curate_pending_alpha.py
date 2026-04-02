from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha.candidate_review import merge_flagged_rules, split_review_candidates
from utils.file_io import read_json_file, write_json_atomic

PENDING_FILE = Path("alpha/output/pending_rules.json")
FLAGGED_FILE = Path("alpha/output/flagged_rules.json")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-review current pending alpha rules with the live review gate.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write reviewed pending and flagged rules back to disk.",
    )
    args = parser.parse_args()

    pending = read_json_file(PENDING_FILE, [])
    flagged = read_json_file(FLAGGED_FILE, [])
    if not isinstance(pending, list):
        raise SystemExit(f"pending rules file is not a list: {PENDING_FILE}")
    if not isinstance(flagged, list):
        flagged = []

    kept, new_flags, reason_counts = split_review_candidates(pending)
    merged_flagged = merge_flagged_rules(flagged, new_flags)

    print(f"pending_before={len(pending)}")
    print(f"pending_after={len(kept)}")
    print(f"removed={len(new_flags)}")
    print(f"flagged_after={len(merged_flagged)}")
    print("kept_rules:")
    for rule in kept:
        stats = rule.get("stats", {})
        avg = stats.get("oos_net_return", stats.get("oos_avg_ret", 0.0))
        print(
            "  - "
            f"{rule.get('id')} | {rule.get('rule_str')} | "
            f"{rule.get('mechanism_type')} | "
            f"WR={float(stats.get('oos_win_rate', 0.0)):.2f}% "
            f"n={int(stats.get('n_oos', 0) or 0)} "
            f"avg={float(avg or 0.0):.4f}%"
        )

    print("top_reasons:")
    for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0])):
        print(f"  - {count}x {reason}")

    if args.apply:
        write_json_atomic(PENDING_FILE, kept, ensure_ascii=False, indent=2)
        write_json_atomic(FLAGGED_FILE, merged_flagged, ensure_ascii=False, indent=2)
        print("applied=true")
    else:
        print("applied=false")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
