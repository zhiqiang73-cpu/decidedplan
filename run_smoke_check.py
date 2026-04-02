"""30-second smoke check entrypoint."""

from __future__ import annotations

import argparse

from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime()

from diagnostics.smoke import format_smoke_report, run_smoke_suite


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a fast smoke check across watchdog/monitor/discovery/downloader")
    parser.add_argument(
        "--strict-network",
        action="store_true",
        help="Treat Binance REST/WS reachability failures as blocking errors",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_smoke_suite(strict_network=args.strict_network)
    print(format_smoke_report(report))
    return 1 if report.has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
