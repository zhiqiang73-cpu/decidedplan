"""System preflight entrypoint."""

from __future__ import annotations

import sys
from runtime_bootstrap import bootstrap_runtime
bootstrap_runtime()

from diagnostics.doctor import format_report, run_preflight_checks


def main() -> int:
    report = run_preflight_checks()
    print(format_report(report))
    return 1 if report.has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

