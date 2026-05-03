"""CLI entrypoint for mlbreview.

Usage:
    python -m mlbreview                       # production: run for yesterday in ET
    python -m mlbreview --dry-run             # local iteration: no email, no publish
    python -m mlbreview --date 2025-08-15     # backfill a specific date
    python -m mlbreview --out-dir ./public    # where rendered files land

Pipeline orchestration arrives in U6; for now this validates that the package
imports cleanly, parses flags, and surfaces config errors helpfully.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date as date_cls, datetime, timedelta
from zoneinfo import ZoneInfo

from mlbreview.config import Config

logger = logging.getLogger("mlbreview")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mlbreview",
        description="Generate and send the daily MLB digest.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline without sending email or publishing dashboard.",
    )
    parser.add_argument(
        "--date",
        type=lambda s: date_cls.fromisoformat(s),
        default=None,
        help="ISO date (YYYY-MM-DD) to generate digest for. Defaults to yesterday in ET.",
    )
    parser.add_argument(
        "--out-dir",
        default="./public",
        help="Directory to write rendered dashboard HTML into. Default: ./public",
    )
    return parser.parse_args(argv)


def _default_date_et() -> date_cls:
    """Yesterday's date in America/New_York at run time."""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    return now_et.date() - timedelta(days=1)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _parse_args(argv)
    target_date = args.date or _default_date_et()

    # Loading config validates env state. Dry-run tolerates missing secrets.
    Config.load(require_secrets=not args.dry_run)

    logger.info(
        "mlbreview starting (date=%s, dry_run=%s, out_dir=%s)",
        target_date.isoformat(),
        args.dry_run,
        args.out_dir,
    )

    # Pipeline lands in U6.
    logger.info("Pipeline not yet implemented (arrives in U6).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
