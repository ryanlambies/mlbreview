"""CLI entrypoint for mlbreview.

Usage:
    python -m mlbreview                       # production: run for yesterday in ET
    python -m mlbreview --dry-run             # local iteration: no email, no publish
    python -m mlbreview --date 2025-08-15     # backfill a specific date
    python -m mlbreview --out-dir ./public    # where rendered files land
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date as date_cls, datetime, timedelta
from zoneinfo import ZoneInfo

from mlbreview.config import Config
from mlbreview.pipeline import run

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
    parser.add_argument(
        "--skip-dst-guard",
        action="store_true",
        help="Skip the DST/hour guard. Used by workflow_dispatch for manual runs.",
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

    config = Config.load(require_secrets=not args.dry_run)

    logger.info(
        "mlbreview starting (date=%s, dry_run=%s, out_dir=%s)",
        target_date.isoformat(),
        args.dry_run,
        args.out_dir,
    )

    return run(
        target_date,
        dry_run=args.dry_run,
        out_dir=args.out_dir,
        config=config,
        skip_dst_guard=args.skip_dst_guard,
    )


if __name__ == "__main__":
    sys.exit(main())
