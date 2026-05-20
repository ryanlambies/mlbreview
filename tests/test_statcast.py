"""Tests for the Statcast / pybaseball integration layer."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from mlbreview.data.statcast import (
    StatcastHitter,
    StatcastPitcher,
    fetch_statcast_hitters,
    fetch_statcast_pitchers,
    parse_hitter_dataframe,
    parse_pitcher_dataframe,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixture helpers — load saved FanGraphs data as DataFrames
# ---------------------------------------------------------------------------


def _load_batting_df():
    """Load the batting fixture as a pandas DataFrame."""
    import pandas as pd

    data = json.loads((FIXTURES / "fg_batting_statcast.json").read_text())
    return pd.DataFrame(data)


def _load_pitching_df():
    """Load the pitching fixture as a pandas DataFrame."""
    import pandas as pd

    data = json.loads((FIXTURES / "fg_pitching_statcast.json").read_text())
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# Dataclass basics
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_statcast_hitter_is_frozen(self):
        h = StatcastHitter(
            name="Test", team="TST", xwoba=0.350, barrel_pct=10.0, hard_hit_pct=40.0
        )
        with pytest.raises(AttributeError):
            h.xwoba = 0.400  # type: ignore[misc]

    def test_statcast_pitcher_is_frozen(self):
        p = StatcastPitcher(
            name="Test", team="TST", fip=3.0, xfip=3.2, xera=3.1,
            barrel_pct=6.0, hard_hit_pct=30.0,
        )
        with pytest.raises(AttributeError):
            p.fip = 2.5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DataFrame parsing — hitters
# ---------------------------------------------------------------------------


class TestParseHitterDataframe:
    def test_parses_all_rows(self):
        df = _load_batting_df()
        result = parse_hitter_dataframe(df)

        assert len(result) == 4
        assert "Shohei Ohtani" in result
        assert "Aaron Judge" in result
        assert "Bobby Witt Jr." in result
        assert "Elly De La Cruz" in result

    def test_values_match_fixture(self):
        df = _load_batting_df()
        result = parse_hitter_dataframe(df)

        ohtani = result["Shohei Ohtani"]
        assert ohtani.team == "LAD"
        assert ohtani.xwoba == 0.418
        assert ohtani.barrel_pct == 18.5
        assert ohtani.hard_hit_pct == 52.3

    def test_keyed_by_name(self):
        df = _load_batting_df()
        result = parse_hitter_dataframe(df)

        for name, hitter in result.items():
            assert name == hitter.name

    def test_empty_dataframe(self):
        import pandas as pd

        df = pd.DataFrame()
        assert parse_hitter_dataframe(df) == {}

    def test_missing_columns(self):
        import pandas as pd

        # DataFrame with some but not all required columns
        df = pd.DataFrame([{"Name": "Test", "Team": "TST", "AVG": 0.300}])
        assert parse_hitter_dataframe(df) == {}

    def test_none_input(self):
        assert parse_hitter_dataframe(None) == {}  # type: ignore[arg-type]

    def test_non_dataframe_input(self):
        assert parse_hitter_dataframe("not a dataframe") == {}  # type: ignore[arg-type]

    def test_skips_rows_with_bad_values(self):
        import pandas as pd

        data = [
            {"Name": "Good Player", "Team": "TST", "xwOBA": 0.350,
             "Barrel%": 10.0, "HardHit%": 40.0},
            {"Name": "Bad Player", "Team": "TST", "xwOBA": "not_a_number",
             "Barrel%": 10.0, "HardHit%": 40.0},
        ]
        df = pd.DataFrame(data)
        result = parse_hitter_dataframe(df)

        assert len(result) == 1
        assert "Good Player" in result

    def test_skips_rows_with_nan_values(self):
        import pandas as pd
        import numpy as np

        data = [
            {"Name": "Good", "Team": "TST", "xwOBA": 0.350,
             "Barrel%": 10.0, "HardHit%": 40.0},
            {"Name": "NaN xwOBA", "Team": "TST", "xwOBA": np.nan,
             "Barrel%": 10.0, "HardHit%": 40.0},
            {"Name": "NaN Barrel", "Team": "TST", "xwOBA": 0.300,
             "Barrel%": np.nan, "HardHit%": 40.0},
        ]
        df = pd.DataFrame(data)
        result = parse_hitter_dataframe(df)

        assert len(result) == 1
        assert "Good" in result
        assert "NaN xwOBA" not in result
        assert "NaN Barrel" not in result

    def test_all_rows_fail_parsing(self):
        """DataFrame with correct columns but all unparseable values."""
        import pandas as pd

        data = [
            {"Name": "Bad1", "Team": "TST", "xwOBA": "not_a_number",
             "Barrel%": "bad", "HardHit%": "bad"},
            {"Name": "Bad2", "Team": "TST", "xwOBA": "nope",
             "Barrel%": "nah", "HardHit%": "nah"},
        ]
        df = pd.DataFrame(data)
        result = parse_hitter_dataframe(df)

        assert result == {}


# ---------------------------------------------------------------------------
# DataFrame parsing — pitchers
# ---------------------------------------------------------------------------


class TestParsePitcherDataframe:
    def test_parses_all_rows(self):
        df = _load_pitching_df()
        result = parse_pitcher_dataframe(df)

        assert len(result) == 5
        assert "Gerrit Cole" in result
        assert "Tarik Skubal" in result
        assert "Emmanuel Clase" in result
        assert "Ryan Helsley" in result
        assert "Struggling Pitcher" in result

    def test_values_match_fixture(self):
        df = _load_pitching_df()
        result = parse_pitcher_dataframe(df)

        cole = result["Gerrit Cole"]
        assert cole.team == "NYY"
        assert cole.fip == 2.90
        assert cole.xfip == 3.10
        assert cole.xera == 2.85
        assert cole.barrel_pct == 5.8
        assert cole.hard_hit_pct == 30.2

    def test_keyed_by_name(self):
        df = _load_pitching_df()
        result = parse_pitcher_dataframe(df)

        for name, pitcher in result.items():
            assert name == pitcher.name

    def test_empty_dataframe(self):
        import pandas as pd

        df = pd.DataFrame()
        assert parse_pitcher_dataframe(df) == {}

    def test_missing_columns(self):
        import pandas as pd

        df = pd.DataFrame([{"Name": "Test", "Team": "TST", "ERA": 3.50}])
        assert parse_pitcher_dataframe(df) == {}

    def test_none_input(self):
        assert parse_pitcher_dataframe(None) == {}  # type: ignore[arg-type]

    def test_skips_rows_with_bad_values(self):
        import pandas as pd

        data = [
            {"Name": "Good", "Team": "TST", "FIP": 3.0, "xFIP": 3.2,
             "xERA": 3.1, "Barrel%": 6.0, "HardHit%": 30.0},
            {"Name": "Bad", "Team": "TST", "FIP": "nope", "xFIP": 3.2,
             "xERA": 3.1, "Barrel%": 6.0, "HardHit%": 30.0},
        ]
        df = pd.DataFrame(data)
        result = parse_pitcher_dataframe(df)

        assert len(result) == 1
        assert "Good" in result

    def test_skips_rows_with_nan_values(self):
        import pandas as pd
        import numpy as np

        data = [
            {"Name": "Good", "Team": "TST", "FIP": 3.0, "xFIP": 3.2,
             "xERA": 3.1, "Barrel%": 6.0, "HardHit%": 30.0},
            {"Name": "NaN FIP", "Team": "TST", "FIP": np.nan, "xFIP": 3.2,
             "xERA": 3.1, "Barrel%": 6.0, "HardHit%": 30.0},
        ]
        df = pd.DataFrame(data)
        result = parse_pitcher_dataframe(df)

        assert len(result) == 1
        assert "Good" in result
        assert "NaN FIP" not in result

    def test_all_rows_fail_parsing(self):
        """DataFrame with correct columns but all unparseable values."""
        import pandas as pd

        data = [
            {"Name": "Bad", "Team": "TST", "FIP": "x", "xFIP": "x",
             "xERA": "x", "Barrel%": "x", "HardHit%": "x"},
        ]
        df = pd.DataFrame(data)
        result = parse_pitcher_dataframe(df)

        assert result == {}


# ---------------------------------------------------------------------------
# Fetch functions — graceful degradation
# ---------------------------------------------------------------------------


def _mock_pybaseball(*, fg_batting_data=None, fg_pitching_data=None) -> ModuleType:
    """Build a fake pybaseball module with controllable fetch functions."""
    mod = ModuleType("pybaseball")
    if fg_batting_data is not None:
        mod.fg_batting_data = fg_batting_data  # type: ignore[attr-defined]
    if fg_pitching_data is not None:
        mod.fg_pitching_data = fg_pitching_data  # type: ignore[attr-defined]
    return mod


class _PatchPybaseball:
    """Context manager that swaps pybaseball in sys.modules, restoring on exit."""

    def __init__(self, module: object):
        self._module = module
        self._original: object = None
        self._had_original = False

    def __enter__(self):
        self._original = sys.modules.get("pybaseball")
        self._had_original = "pybaseball" in sys.modules
        sys.modules["pybaseball"] = self._module  # type: ignore[assignment]
        return self

    def __exit__(self, *_):
        if self._had_original:
            sys.modules["pybaseball"] = self._original  # type: ignore[assignment]
        else:
            sys.modules.pop("pybaseball", None)


class TestFetchStatcastHitters:
    def test_happy_path(self):
        """Full fetch→parse pipeline with fixture data via mocked pybaseball."""
        fixture_df = _load_batting_df()
        mock_mod = _mock_pybaseball(
            fg_batting_data=lambda **kwargs: fixture_df,
        )

        with _PatchPybaseball(mock_mod):
            result = fetch_statcast_hitters(2026)

        assert len(result) == 4
        assert "Shohei Ohtani" in result
        assert result["Shohei Ohtani"].xwoba == 0.418
        assert "Aaron Judge" in result

    def test_pybaseball_import_error(self):
        """When pybaseball is not installed, returns empty dict."""
        with _PatchPybaseball(None):
            result = fetch_statcast_hitters(2026)
            assert result == {}

    def test_fetch_exception_returns_empty(self):
        """When the FanGraphs scrape fails, returns empty dict."""
        def raise_error(**kwargs):
            raise ConnectionError("FanGraphs down")

        mock_mod = _mock_pybaseball(fg_batting_data=raise_error)

        with _PatchPybaseball(mock_mod):
            result = fetch_statcast_hitters(2026)
            assert result == {}

    def test_fetch_returns_none(self):
        """When pybaseball returns None instead of a DataFrame."""
        mock_mod = _mock_pybaseball(fg_batting_data=lambda **kwargs: None)

        with _PatchPybaseball(mock_mod):
            result = fetch_statcast_hitters(2026)
            assert result == {}


class TestFetchStatcastPitchers:
    def test_happy_path(self):
        """Full fetch→parse pipeline with fixture data via mocked pybaseball."""
        fixture_df = _load_pitching_df()
        mock_mod = _mock_pybaseball(
            fg_pitching_data=lambda **kwargs: fixture_df,
        )

        with _PatchPybaseball(mock_mod):
            result = fetch_statcast_pitchers(2026)

        assert len(result) == 5
        assert "Gerrit Cole" in result
        assert result["Gerrit Cole"].fip == 2.90
        assert result["Tarik Skubal"].xera == 2.40

    def test_pybaseball_import_error(self):
        """When pybaseball is not installed, returns empty dict."""
        with _PatchPybaseball(None):
            result = fetch_statcast_pitchers(2026)
            assert result == {}

    def test_fetch_exception_returns_empty(self):
        """When the FanGraphs scrape fails, returns empty dict."""
        def raise_error(**kwargs):
            raise ConnectionError("FanGraphs down")

        mock_mod = _mock_pybaseball(fg_pitching_data=raise_error)

        with _PatchPybaseball(mock_mod):
            result = fetch_statcast_pitchers(2026)
            assert result == {}

    def test_fetch_returns_none(self):
        """When pybaseball returns None instead of a DataFrame."""
        mock_mod = _mock_pybaseball(fg_pitching_data=lambda **kwargs: None)

        with _PatchPybaseball(mock_mod):
            result = fetch_statcast_pitchers(2026)
            assert result == {}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_duplicate_names_last_wins(self):
        """If two rows have the same name, last entry wins (dict semantics)."""
        import pandas as pd

        data = [
            {"Name": "Duplicate", "Team": "AAA", "xwOBA": 0.300,
             "Barrel%": 8.0, "HardHit%": 35.0},
            {"Name": "Duplicate", "Team": "BBB", "xwOBA": 0.400,
             "Barrel%": 15.0, "HardHit%": 50.0},
        ]
        df = pd.DataFrame(data)
        result = parse_hitter_dataframe(df)

        assert len(result) == 1
        assert result["Duplicate"].team == "BBB"
        assert result["Duplicate"].xwoba == 0.400

    def test_extra_columns_ignored(self):
        """Extra columns in the DataFrame don't cause errors."""
        import pandas as pd

        data = [
            {"Name": "Test", "Team": "TST", "xwOBA": 0.350,
             "Barrel%": 10.0, "HardHit%": 40.0, "ExtraCol": "ignored",
             "AnotherExtra": 999},
        ]
        df = pd.DataFrame(data)
        result = parse_hitter_dataframe(df)

        assert len(result) == 1
        assert result["Test"].xwoba == 0.350

    def test_single_row_dataframe(self):
        """Works with a single-row DataFrame."""
        import pandas as pd

        data = [
            {"Name": "Solo", "Team": "TST", "FIP": 3.0, "xFIP": 3.2,
             "xERA": 3.1, "Barrel%": 6.0, "HardHit%": 30.0},
        ]
        df = pd.DataFrame(data)
        result = parse_pitcher_dataframe(df)

        assert len(result) == 1
        assert result["Solo"].fip == 3.0
