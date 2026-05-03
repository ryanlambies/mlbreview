"""Capture MLB Stats API JSON fixtures used by the test suite.

Hits the live API once to record real payloads we can replay deterministically
in tests. Fixtures land in `tests/fixtures/`. Re-run when the API contract
changes or when adding new test scenarios.

Usage:
    python scripts/capture_fixtures.py
    python scripts/capture_fixtures.py --schedule-date 2025-08-15

The script picks a walkoff candidate (one-run home win in the 9th-or-later) and
a blowout candidate (8+ run margin) from the chosen schedule date. It also
records a small transactions window. All output is pretty-printed JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

# Make src importable when run as `python scripts/capture_fixtures.py`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mlbreview.data.client import (  # noqa: E402
    BASE_URL_V1,
    get_json,
    make_client,
)

FIXTURES_DIR = ROOT / "tests" / "fixtures"


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size:,} bytes)")


def capture_schedule(client, target: date) -> dict:
    payload = get_json(
        client,
        f"{BASE_URL_V1}/schedule",
        params={
            "sportId": 1,
            "date": target.isoformat(),
            "hydrate": "team,linescore,decisions",
        },
    )
    _write(FIXTURES_DIR / f"schedule_{target.isoformat()}.json", payload)
    return payload


def capture_tonight(client, target: date) -> dict:
    payload = get_json(
        client,
        f"{BASE_URL_V1}/schedule",
        params={
            "sportId": 1,
            "date": target.isoformat(),
            "hydrate": "team,linescore,probablePitcher,broadcasts",
        },
    )
    _write(FIXTURES_DIR / f"tonight_{target.isoformat()}.json", payload)
    return payload


def capture_winprob(client, gamePk: int, label: str) -> list:
    payload = get_json(client, f"{BASE_URL_V1}/game/{gamePk}/winProbability")
    out = FIXTURES_DIR / f"winprob_{label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size:,} bytes)")
    return payload


def capture_transactions(client, start: date, end: date) -> dict:
    payload = get_json(
        client,
        f"{BASE_URL_V1}/transactions",
        params={
            "sportId": 1,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
        },
    )
    _write(FIXTURES_DIR / "transactions_sample.json", payload)
    return payload


def _final_games(schedule_payload: dict) -> list[dict]:
    games: list[dict] = []
    for date_block in schedule_payload.get("dates", []):
        for g in date_block.get("games", []):
            status = g.get("status", {}).get("detailedState", "")
            if status in ("Final", "Game Over", "Completed Early"):
                games.append(g)
    return games


def _pick_walkoff_and_blowout(games: list[dict]) -> tuple[int | None, int | None]:
    """Return (walkoff_gamePk, blowout_gamePk).

    Walkoff heuristic: home team wins by 1 in regulation 9th OR any extra
    innings. Falls back to "closest one-run home win" if no walkoff matches.
    Blowout heuristic: largest margin of victory.
    """
    walkoff: tuple[int, int] | None = None  # (innings, gamePk)
    one_run_home_win_fallback: int | None = None
    biggest_margin: tuple[int, int] | None = None  # (margin, gamePk)

    for g in games:
        teams = g.get("teams", {})
        away_score = int(teams.get("away", {}).get("score") or 0)
        home_score = int(teams.get("home", {}).get("score") or 0)
        margin = abs(away_score - home_score)
        gamePk = g["gamePk"]
        innings = int(
            g.get("linescore", {}).get("currentInning")
            or len(g.get("linescore", {}).get("innings") or [])
            or 9
        )

        if home_score > away_score and margin == 1 and innings >= 9:
            if walkoff is None or innings > walkoff[0]:
                walkoff = (innings, gamePk)
            if one_run_home_win_fallback is None:
                one_run_home_win_fallback = gamePk

        if biggest_margin is None or margin > biggest_margin[0]:
            biggest_margin = (margin, gamePk)

    walkoff_pk = walkoff[1] if walkoff else one_run_home_win_fallback
    blowout_pk = biggest_margin[1] if biggest_margin else None
    return walkoff_pk, blowout_pk


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schedule-date",
        type=lambda s: date.fromisoformat(s),
        default=date(2025, 8, 15),
        help="Date of completed games to capture (default 2025-08-15).",
    )
    parser.add_argument(
        "--tonight-date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Date for tonight-preview capture. Default: schedule-date + 1.",
    )
    args = parser.parse_args(argv)

    schedule_date: date = args.schedule_date
    tonight_date: date = args.tonight_date or (schedule_date + timedelta(days=1))

    with make_client() as client:
        schedule_payload = capture_schedule(client, schedule_date)
        capture_tonight(client, tonight_date)

        finals = _final_games(schedule_payload)
        if not finals:
            print(f"no completed games on {schedule_date}, skipping feed/live captures")
        else:
            walkoff_pk, blowout_pk = _pick_walkoff_and_blowout(finals)
            if walkoff_pk:
                capture_winprob(client, walkoff_pk, "walkoff")
            else:
                print("no walkoff candidate found")
            if blowout_pk and blowout_pk != walkoff_pk:
                capture_winprob(client, blowout_pk, "blowout")
            else:
                print("no distinct blowout candidate found")

        capture_transactions(
            client,
            schedule_date - timedelta(days=1),
            schedule_date,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
