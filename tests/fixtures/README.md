# Test fixtures

Recorded MLB Stats API JSON committed once and replayed by the test suite. No
live network in CI.

| File | Source endpoint | Purpose |
|---|---|---|
| `schedule_2025-08-15.json` | `/api/v1/schedule?sportId=1&date=2025-08-15&hydrate=team,linescore,decisions` | 15 completed regular-season games for `fetch_finals` parsing tests |
| `tonight_2025-08-16.json` | `/api/v1/schedule?sportId=1&date=2025-08-16&hydrate=team,linescore,probablePitcher,broadcasts` | Probable pitchers + national broadcast flags for `fetch_tonight` |
| `winprob_walkoff.json` | `/api/v1/game/{gamePk}/winProbability` (one-run home win, late-inning swing) | High-drama feed: max \|WPA\| ~34.6, late-inning swing same play |
| `winprob_blowout.json` | `/api/v1/game/{gamePk}/winProbability` (largest margin of the day) | Low-drama feed: max \|WPA\| ~13, late max ~4.6 |
| `transactions_sample.json` | `/api/v1/transactions?sportId=1&startDate=2025-08-14&endDate=2025-08-15` | 91 MLB-only transactions covering call-ups + IL moves |

## Re-capturing

The fixtures are read-only baseball data, captured once, committed verbatim.
To regenerate (e.g. after an API contract change):

```bash
python scripts/capture_fixtures.py
# or pin a specific date:
python scripts/capture_fixtures.py --schedule-date 2025-08-15
```

The script picks a walkoff (one-run home win, latest possible inning) and a
blowout (largest margin) from the chosen date and pulls their
`/winProbability` payloads. Update test assertions when fixture data changes.
