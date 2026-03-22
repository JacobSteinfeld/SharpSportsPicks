#!/usr/bin/env python3
"""
SharpSportsPicks AI — MLB Model Backtester
==========================================
Validates the SIERA/xFIP/wRC+ projection model and compares projections
against real Vegas closing totals to simulate actual betting ROI.

Usage:
  python backtest.py                    → 40 real 2025 games (no Vegas data)
  python backtest.py --season 2021      → 2021 games WITH Vegas odds comparison
  python backtest.py --n 80 --season 2021 --edge 1.0   → 80 games, bet when model
                                                           diverges ≥1.0 run from Vegas
  python backtest.py --quiet --season 2021              → summary only

Install: pip install pybaseball==2.2.7 pandas openpyxl
Vegas data: SBR free archives available for 2011-2021 (auto-downloaded).
            For 2022+ use --odds-csv to supply your own CSV with columns:
            date,away_team,home_team,close_total
"""

import argparse
import sys
import warnings
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

warnings.filterwarnings("ignore")

try:
    import pandas as pd
    import pybaseball as pb
    pb.cache.enable()
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: pip install pybaseball==2.2.7 pandas openpyxl")
    sys.exit(1)


# ── Season-level data cache (loaded once per run) ─────────────────────────────
# Avoids repeated API calls. Fangraphs pitching_stats / batting_stats give the
# full-season SIERA, xFIP, and wRC+ values we need.

_PITCH_CACHE: Optional[pd.DataFrame] = None
_BAT_CACHE:   Optional[pd.DataFrame] = None


def _load_pitching(year: int) -> pd.DataFrame:
    global _PITCH_CACHE
    if _PITCH_CACHE is None:
        print(f"  → Loading {year} Fangraphs pitching stats (SIERA/xFIP)...", flush=True)
        _PITCH_CACHE = pb.pitching_stats(year, qual=0)
        if _PITCH_CACHE is None or _PITCH_CACHE.empty:
            print("  ✗ Could not load pitching stats from Fangraphs.")
            _PITCH_CACHE = pd.DataFrame()
    return _PITCH_CACHE


def _load_batting(year: int) -> pd.DataFrame:
    global _BAT_CACHE
    if _BAT_CACHE is None:
        print(f"  → Loading {year} Fangraphs batting stats (wRC+)...", flush=True)
        _BAT_CACHE = pb.batting_stats(year, qual=0)
        if _BAT_CACHE is None or _BAT_CACHE.empty:
            print("  ✗ Could not load batting stats from Fangraphs.")
            _BAT_CACHE = pd.DataFrame()
    return _BAT_CACHE


# ── Vegas Odds (SBR archives) ─────────────────────────────────────────────────
# SBR publishes free historical MLB odds through 2021.
# Teams use SBR abbreviations which differ from our standard ones.

SBR_BASE_URL = "https://www.sportsbookreviewsonline.com/wp-content/uploads/sportsbookreviewsonline_com_737"

# SBR team name → our standard abbreviation
SBR_TO_STD = {
    "CUB": "CHC", "SDG": "SDP", "SFO": "SFG", "CWS": "CHW",
    "KAN": "KCR", "TAM": "TBR", "WAS": "WSN",
}

# Our standard abbreviation → SBR team name (reverse of above + passthrough)
STD_TO_SBR = {v: k for k, v in SBR_TO_STD.items()}

_ODDS_CACHE: Optional[pd.DataFrame] = None


def _sbr_date_int(date_str: str) -> int:
    """Convert '2021-04-15' → 415  |  '2021-10-03' → 1003."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.month * 100 + dt.day


def _to_sbr(abbr: str) -> str:
    return STD_TO_SBR.get(abbr.upper(), abbr.upper())


def load_sbr_odds(year: int, odds_csv: Optional[str] = None) -> pd.DataFrame:
    """
    Load Vegas closing totals.
    - If odds_csv provided: load from local CSV (date,away_team,home_team,close_total)
    - If SBR year (2011-2021): auto-download from SBR archives
    - Otherwise: return empty DataFrame (no odds comparison)
    """
    global _ODDS_CACHE
    if _ODDS_CACHE is not None:
        return _ODDS_CACHE

    # Manual CSV override
    if odds_csv:
        try:
            _ODDS_CACHE = pd.read_csv(odds_csv)
            print(f"  → Loaded Vegas odds from {odds_csv} ({len(_ODDS_CACHE)} rows)")
            return _ODDS_CACHE
        except Exception as e:
            print(f"  ⚠  Could not load odds CSV: {e}")
            _ODDS_CACHE = pd.DataFrame()
            return _ODDS_CACHE

    # SBR free archive (2011-2021)
    if year < 2011 or year > 2021:
        print(f"  ℹ  Vegas odds: SBR archives only cover 2011-2021. Use --odds-csv for {year}.")
        _ODDS_CACHE = pd.DataFrame()
        return _ODDS_CACHE

    import urllib.request, io
    url = f"{SBR_BASE_URL}/mlb-odds-{year}.xlsx"
    print(f"  → Downloading SBR {year} MLB odds...", flush=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
        df = pd.read_excel(io.BytesIO(raw))
        # Rename the unnamed juice columns for clarity
        df = df.rename(columns={
            "Unnamed: 18": "RunLineJuice",
            "Unnamed: 20": "OpenOUJuice",
            "Unnamed: 22": "CloseOUJuice",
        })
        print(f"  → SBR odds loaded: {len(df)} rows ({len(df)//2} games)")
        _ODDS_CACHE = df
        return _ODDS_CACHE
    except Exception as e:
        print(f"  ⚠  Could not download SBR odds: {e}")
        _ODDS_CACHE = pd.DataFrame()
        return _ODDS_CACHE


def get_vegas_total(date_str: str, away_abbr: str, home_abbr: str,
                    odds_df: pd.DataFrame) -> Optional[float]:
    """
    Look up the closing total for a game from SBR odds data.
    Returns the CloseOU float or None if not found.
    """
    if odds_df.empty:
        return None

    # SBR date format: MMDD as int (no leading zero for month)
    date_int = _sbr_date_int(date_str)
    sbr_away = _to_sbr(away_abbr)
    sbr_home = _to_sbr(home_abbr)

    # Find visitor row for this game
    mask = (
        (odds_df["Date"] == date_int) &
        (odds_df["VH"] == "V") &
        (odds_df["Team"].str.upper() == sbr_away)
    )
    rows = odds_df[mask]

    if rows.empty:
        # Try home team as anchor instead
        mask2 = (
            (odds_df["Date"] == date_int) &
            (odds_df["VH"] == "H") &
            (odds_df["Team"].str.upper() == sbr_home)
        )
        rows = odds_df[mask2]

    if rows.empty:
        return None

    val = rows.iloc[0]["CloseOU"]
    try:
        return float(val) if val and str(val) != "nan" else None
    except (ValueError, TypeError):
        return None


# ── Betting simulation ─────────────────────────────────────────────────────────

def simulate_bet(model_proj: float, vegas_line: float, actual_total: int,
                 edge_threshold: float = 1.5) -> Optional[Dict]:
    """
    Simulate one over/under bet.
    edge = model_proj - vegas_line
    |edge| >= edge_threshold → place bet
    Juice: -110 standard (win 1.0u, lose 1.1u)
    """
    edge = round(model_proj - vegas_line, 2)
    if abs(edge) < edge_threshold:
        return None  # no edge, no bet

    bet_side = "OVER" if edge > 0 else "UNDER"

    if actual_total == vegas_line:
        return {"side": bet_side, "edge": edge, "outcome": "PUSH", "units": 0.0}

    win = (bet_side == "OVER" and actual_total > vegas_line) or \
          (bet_side == "UNDER" and actual_total < vegas_line)

    return {
        "side":   bet_side,
        "edge":   edge,
        "outcome": "WIN" if win else "LOSS",
        "units":   1.0 if win else -1.1,
    }


def print_betting_summary(results: List[Dict], edge_threshold: float) -> None:
    """Print full betting ROI analysis."""
    bets = [r for r in results if r.get("bet") is not None]
    if not bets:
        print(f"\n  No bets placed (no Vegas odds data or no edges ≥ {edge_threshold} runs).")
        return

    wins   = sum(1 for b in bets if b["bet"]["outcome"] == "WIN")
    losses = sum(1 for b in bets if b["bet"]["outcome"] == "LOSS")
    pushes = sum(1 for b in bets if b["bet"]["outcome"] == "PUSH")
    total  = wins + losses + pushes
    units  = sum(b["bet"]["units"] for b in bets)
    wp     = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
    roi    = units / total * 100 if total > 0 else 0

    print(f"\n{'═'*62}")
    print(f"  BETTING SIMULATION vs Vegas Closing Total")
    print(f"  Edge threshold: ≥ {edge_threshold} runs | Juice: -110 standard")
    print(f"{'═'*62}")
    print(f"  Bets:       {total}  (W {wins}  L {losses}  P {pushes})")
    print(f"  Win rate:   {wp:.1f}%  (break-even = 52.4%)")
    print(f"  Units:      {units:+.2f}u")
    print(f"  ROI:        {roi:+.1f}%  {'✅ PROFITABLE' if roi > 0 else '❌ LOSING'}")

    # By edge tier
    print(f"\n  BY EDGE TIER:")
    tiers = [
        ("0.5-1.0", 0.5, 1.0),
        ("1.0-1.5", 1.0, 1.5),
        ("1.5-2.0", 1.5, 2.0),
        ("2.0-2.5", 2.0, 2.5),
        ("2.5+",    2.5, 99),
    ]
    for label, lo, hi in tiers:
        tier_bets = [b for b in bets if lo <= abs(b["bet"]["edge"]) < hi]
        if not tier_bets:
            continue
        tw = sum(1 for b in tier_bets if b["bet"]["outcome"] == "WIN")
        tl = sum(1 for b in tier_bets if b["bet"]["outcome"] == "LOSS")
        tu = sum(b["bet"]["units"] for b in tier_bets)
        twp = tw / (tw + tl) * 100 if (tw + tl) > 0 else 0
        print(f"    Edge {label:>8}  {len(tier_bets):>3} bets  W{tw}/L{tl}  "
              f"WR {twp:.0f}%  {tu:+.1f}u")

    # Over vs Under split
    print(f"\n  OVER vs UNDER:")
    for side in ["OVER", "UNDER"]:
        side_bets = [b for b in bets if b["bet"]["side"] == side]
        if not side_bets:
            continue
        sw = sum(1 for b in side_bets if b["bet"]["outcome"] == "WIN")
        sl = sum(1 for b in side_bets if b["bet"]["outcome"] == "LOSS")
        su = sum(b["bet"]["units"] for b in side_bets)
        swp = sw / (sw + sl) * 100 if (sw + sl) > 0 else 0
        print(f"    {side:<6}  {len(side_bets):>3} bets  W{sw}/L{sl}  WR {swp:.0f}%  {su:+.1f}u")

    # Individual bet log
    print(f"\n  BET LOG:")
    print(f"  {'Date':<12} {'Matchup':<14} {'Proj':>5} {'Vegas':>6} {'Edge':>6} "
          f"{'Side':<6} {'Actual':>7}  Outcome")
    print(f"  {'─'*72}")
    for r in sorted(bets, key=lambda x: x["date"]):
        b = r["bet"]
        sym = "✅" if b["outcome"] == "WIN" else ("⬜" if b["outcome"] == "PUSH" else "❌")
        print(f"  {r['date']:<12} {r['matchup']:<14} {r['proj_total']:>5.1f} "
              f"{r['vegas_total']:>6.1f} {b['edge']:>+6.1f} {b['side']:<6} "
              f"{r['actual_total']:>7}  {sym} {b['outcome']}")

    print(f"{'═'*62}")


# ── Park Factors (2025 approximations, Fangraphs 3-year avg) ─────────────────

PARK_FACTORS = {
    "COL": 115, "BAL": 108, "CIN": 107, "NYY": 107, "BOS": 106,
    "TEX": 105, "HOU": 103, "CHC": 102, "ATL": 101, "PHI": 101,
    "MIN": 100, "DET": 100, "CLE": 99,  "WSH": 99,  "MIL": 98,
    "TOR": 98,  "ARI": 97,  "LAA": 97,  "STL": 96,  "KCR": 96,
    "PIT": 95,  "CHW": 95,  "MIA": 95,  "NYM": 94,  "OAK": 94,
    "LAD": 93,  "TBR": 92,  "SEA": 91,  "SFG": 89,  "SDP": 87,
}

# Fallback park factor for unknown teams
DEFAULT_PARK_FACTOR = 100

# MLB average runs per game (2025)
MLB_AVG_RUNS = 4.5

# Default expected IP per start (conservative)
DEFAULT_STARTER_IP = 5.5

# Default bullpen ERA when we can't pull it
DEFAULT_BULLPEN_ERA = 4.20


# ── Home teams used to build the real-game test pool ─────────────────────────
# The backtester pulls actual 2025 schedule data and uses Win/Loss pitcher
# as a proxy for the starting pitcher. Games where both pitchers appear in
# Fangraphs with GS ≥ 5 are included. Covers diverse park types.

SCHEDULE_TEAMS = [
    "COL", "SFG", "PHI", "HOU", "PIT", "CHC", "NYY",
    "BOS", "ATL", "SEA", "LAD", "MIL", "MIN", "BAL",
]

# Minimum starts to qualify as a "real starter" in Fangraphs
MIN_GS_QUALIFIER = 5


def _parse_br_date(raw: str, year: int) -> Optional[str]:
    """Convert BR date like 'Tuesday, Apr 8' → '2025-04-08'."""
    try:
        raw = raw.strip()
        if "," in raw:
            raw = raw.split(",", 1)[1].strip()
        dt = datetime.strptime(f"{raw} {year}", "%b %d %Y")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def build_real_games(year: int, n: int = 50, seed: int = 42) -> List[tuple]:
    """
    Pull actual schedule data and build a list of real test games.
    Uses Win pitcher (home starter) and Loss pitcher (away starter) as proxies.
    Both pitchers must appear in Fangraphs with GS >= MIN_GS_QUALIFIER.
    """
    import random
    random.seed(seed)

    print(f"  → Building real game pool from {len(SCHEDULE_TEAMS)} team schedules...", flush=True)

    # Get the set of confirmed starters from Fangraphs
    pitch = _load_pitching(year)
    if pitch.empty:
        return []
    starter_names = set(
        pitch[pitch["GS"] >= MIN_GS_QUALIFIER]["Name"].str.strip().str.lower().tolist()
    )

    def is_starter(name: str) -> bool:
        n_low = name.strip().lower()
        last  = n_low.split()[-1] if " " in n_low else n_low
        # Check for last-name match in Fangraphs starter list
        return any(last in s for s in starter_names)

    games = []
    seen  = set()

    for team in SCHEDULE_TEAMS:
        try:
            sched = pb.schedule_and_record(year, team)
            if sched is None or sched.empty:
                continue
            # Home wins only (Win pitcher = home starter, Loss pitcher = away starter)
            home_wins = sched[
                (sched["Home_Away"] == "Home") &
                sched["R"].notna() &
                sched["Win"].notna() &
                sched["Loss"].notna() &
                (sched["W/L"].str.startswith("W", na=False))
            ].copy()

            for _, row in home_wins.iterrows():
                home_p = str(row["Win"]).strip()
                away_p = str(row["Loss"]).strip()
                date_raw = str(row["Date"]).strip()

                # Skip doubleheaders
                if "(" in date_raw:
                    continue

                if not is_starter(home_p) or not is_starter(away_p):
                    continue

                date_str = _parse_br_date(date_raw, year)
                if not date_str:
                    continue

                away_abbr = str(row["Opp"]).strip()
                key = (date_str, away_abbr, team)
                if key in seen:
                    continue
                seen.add(key)

                home_score = int(float(row["R"]  or 0))
                away_score = int(float(row["RA"] or 0))
                total      = home_score + away_score

                games.append({
                    "tuple":       (date_str, away_abbr, team, away_p, home_p),
                    "known_score": (away_score, home_score),
                    "home_score":  home_score,
                    "away_score":  away_score,
                    "total":       total,
                })
        except Exception as e:
            print(f"    ⚠  Schedule pull failed for {team}: {e}")

    if not games:
        print("  ✗ No usable real games found.")
        return []

    print(f"  → Pool: {len(games)} real games found. Sampling {min(n, len(games))}...", flush=True)

    # Stratified sample: low totals (<7), medium (7-12), high (>12)
    low    = [g for g in games if g["total"] < 7]
    medium = [g for g in games if 7 <= g["total"] <= 12]
    high   = [g for g in games if g["total"] > 12]

    # Aim for ~20% low, 60% medium, 20% high
    n_low    = max(1, int(n * 0.20))
    n_high   = max(1, int(n * 0.20))
    n_medium = n - n_low - n_high

    sampled = (
        random.sample(low,    min(n_low,    len(low)))    +
        random.sample(medium, min(n_medium, len(medium))) +
        random.sample(high,   min(n_high,   len(high)))
    )
    random.shuffle(sampled)

    # Return list of (game_tuple, known_score) pairs
    final = sampled[:n]
    return [(g["tuple"], g["known_score"]) for g in final]


# ── Data helpers ──────────────────────────────────────────────────────────────

def _fuzzy_match(df: pd.DataFrame, name: str) -> pd.DataFrame:
    """Flexible last-name-then-first-name matching."""
    parts = name.split()
    last  = parts[-1] if parts else name
    first = parts[0]  if parts else name
    mask  = df["Name"].str.contains(last, case=False, na=False)
    rows  = df[mask]
    if len(rows) > 1:
        # Disambiguate with first name
        m2   = rows["Name"].str.contains(first, case=False, na=False)
        rows = rows[m2] if m2.any() else rows
    if rows.empty:
        mask  = df["Name"].str.contains(first, case=False, na=False)
        rows  = df[mask]
    return rows


def get_pitcher_stats(pitcher_name: str, through_date: str, year: int) -> Optional[Dict]:
    """Look up pitcher from full-season Fangraphs cache."""
    df = _load_pitching(year)
    if df.empty:
        return None

    rows = _fuzzy_match(df, pitcher_name)
    if rows.empty:
        return None

    # If multiple rows (same last name), pick the one with most IP
    if "IP" in rows.columns:
        rows = rows.sort_values("IP", ascending=False)
    r = rows.iloc[0]

    def safe(key, default=None):
        val = r.get(key, default)
        try:
            return float(val) if val is not None and str(val) != "nan" else default
        except (ValueError, TypeError):
            return default

    return {
        "name":       r.get("Name", pitcher_name),
        "team":       r.get("Team", "???"),
        "era":        safe("ERA"),
        "siera":      safe("SIERA"),
        "xfip":       safe("xFIP"),
        "fip":        safe("FIP"),
        "k_pct":      safe("K%"),
        "bb_pct":     safe("BB%"),
        "whip":       safe("WHIP"),
        "hr_fb":      safe("HR/FB"),
        "gb_pct":     safe("GB%"),
        "ip":         safe("IP", 0),
        "gs":         safe("GS", 0),
    }


def get_team_wrc(team_abbr: str, through_date: str, year: int) -> Optional[float]:
    """Team wRC+ weighted by PA from full-season Fangraphs batting stats."""
    df = _load_batting(year)
    if df.empty:
        return None

    # Fangraphs team abbreviations vary — normalize common ones
    TEAM_MAP = {
        "KCR": "KCR", "KAN": "KCR",
        "TBR": "TBR", "TAM": "TBR",
        "SDP": "SDP", "SDG": "SDP",
        "SFG": "SFG", "SFO": "SFG",
        "CHW": "CHW", "CHA": "CHW",
        "CHC": "CHC", "CHN": "CHC",
        "LAD": "LAD", "LAN": "LAD",
        "LAA": "LAA", "ANA": "LAA",
        "WSH": "WSH", "WAS": "WSH",
    }
    lookup = TEAM_MAP.get(team_abbr.upper(), team_abbr.upper())

    team_df = df[df["Team"].str.upper() == lookup]
    if team_df.empty:
        # Broad fallback — partial match
        team_df = df[df["Team"].str.upper().str.startswith(lookup[:2])]
    if team_df.empty:
        return None

    team_df = team_df[team_df["PA"] >= 20].copy()
    if team_df.empty:
        return None

    total_pa = team_df["PA"].sum()
    weighted = (team_df["wRC+"] * team_df["PA"]).sum() / total_pa
    return round(float(weighted), 1)


def get_team_bullpen_era(team_abbr: str, through_date: str, year: int) -> float:
    """Team bullpen ERA from Fangraphs pitching cache (GS < 5 = reliever)."""
    df = _load_pitching(year)
    if df.empty:
        return DEFAULT_BULLPEN_ERA

    TEAM_MAP = {
        "KCR": "KCR", "TBR": "TBR", "SDP": "SDP", "SFG": "SFG",
        "CHW": "CHW", "CHC": "CHC", "LAD": "LAD", "LAA": "LAA",
        "WSH": "WSH",
    }
    lookup = TEAM_MAP.get(team_abbr.upper(), team_abbr.upper())
    team_df = df[df["Team"].str.upper() == lookup]
    if team_df.empty:
        return DEFAULT_BULLPEN_ERA

    relievers = team_df[(team_df["GS"] < 5) & (team_df["IP"] > 5)]
    if relievers.empty:
        return DEFAULT_BULLPEN_ERA

    total_ip = relievers["IP"].sum()
    total_er = (relievers["ERA"] * relievers["IP"] / 9).sum()
    return round(float(total_er / total_ip * 9), 2) if total_ip > 0 else DEFAULT_BULLPEN_ERA


def get_actual_score(date_str: str, away_team: str, home_team: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Pull actual game score from Baseball Reference schedule data.
    Returns (away_runs, home_runs) or (None, None) if not found.
    """
    try:
        year = int(date_str[:4])
        sched = pb.schedule_and_record(year, home_team)
        if sched is None or sched.empty:
            return None, None

        game_date = datetime.strptime(date_str, "%Y-%m-%d")
        # BR dates are like "Friday, May 10" — strip day-of-week, add year
        def parse_br_date(raw):
            try:
                raw = str(raw).strip()
                # Remove leading weekday if present ("Friday, May 10" → "May 10")
                if "," in raw:
                    raw = raw.split(",", 1)[1].strip()
                return datetime.strptime(f"{raw} {year}", "%b %d %Y").date()
            except Exception:
                return None

        sched = sched.copy()
        sched["_date"] = sched["Date"].apply(parse_br_date)

        # Match: home game (Home_Away != "@") on the right date vs correct opponent
        away_abbr_map = {
            "KCR": "KCR", "TBR": "TBR", "SDP": "SDP", "SFG": "SFG",
            "CHW": "CHW", "LAD": "LAD", "WSH": "WSH",
        }
        opp_lookup = away_abbr_map.get(away_team.upper(), away_team.upper())

        row = sched[
            (sched["_date"] == game_date.date()) &
            (sched["Home_Away"] != "@") &       # home game for home_team
            (sched["Opp"].str.upper() == opp_lookup)
        ]
        if row.empty:
            # Try without opp filter (double-header date match)
            row = sched[
                (sched["_date"] == game_date.date()) &
                (sched["Home_Away"] != "@")
            ]
        if row.empty:
            return None, None

        r = row.iloc[0]
        # R = runs scored by this team (home), RA = runs allowed (away scored)
        home_runs = int(float(r["R"]  or 0))
        away_runs = int(float(r["RA"] or 0))
        return away_runs, home_runs
    except Exception:
        return None, None


# ── Model formulas ────────────────────────────────────────────────────────────

def composite_era(siera: float, xfip: float) -> float:
    """SIERA/xFIP blend — core of the pitcher model."""
    return round(0.60 * siera + 0.40 * xfip, 3)


def project_starter_runs(comp_era: float, opp_wrc: float,
                          park_factor: int, expected_ip: float) -> float:
    """Runs allowed by starter through expected IP."""
    adj_era       = comp_era * (opp_wrc / 100)
    park_adj_era  = adj_era  * (park_factor / 100)
    return round((park_adj_era / 9) * expected_ip, 2)


def project_bullpen_runs(bullpen_era: float, starter_ip: float) -> float:
    """Runs allowed by bullpen for remaining innings."""
    bullpen_ip = max(9 - starter_ip, 0)
    return round((bullpen_era / 9) * bullpen_ip, 2)


def project_offense(team_wrc: float, opp_proj_runs_allowed: float,
                    park_factor: int) -> float:
    """Projected runs scored — wRC+ offense model."""
    base_runs     = MLB_AVG_RUNS * (team_wrc / 100)
    pq_factor     = 1 - ((MLB_AVG_RUNS - opp_proj_runs_allowed) / MLB_AVG_RUNS * 0.40)
    adj_runs      = base_runs * pq_factor
    park_adj_runs = adj_runs  * (park_factor / 100)
    return round(park_adj_runs, 2)


def avg_ip_per_start(pitcher_stats: dict) -> float:
    """Estimate expected IP from historical GS data."""
    ip = pitcher_stats.get("ip", 0) or 0
    gs = pitcher_stats.get("gs", 0) or 1
    if gs == 0:
        return DEFAULT_STARTER_IP
    avg = ip / gs
    return round(min(avg, 6.0), 1)


# ── Run one game ──────────────────────────────────────────────────────────────

def run_game(game_tuple: tuple, verbose: bool = True,
             known_score: Optional[Tuple[int, int]] = None,
             vegas_total: Optional[float] = None,
             edge_threshold: float = 1.5) -> Optional[Dict]:
    date_str, away_abbr, home_abbr, away_pitcher, home_pitcher = game_tuple
    year = int(date_str[:4])

    # through_dt is kept for API signature; stats come from full-season cache
    game_date  = datetime.strptime(date_str, "%Y-%m-%d")
    through_dt = (game_date - timedelta(days=1)).strftime("%Y-%m-%d")

    if verbose:
        print(f"\n{'─'*62}")
        print(f"  {away_abbr} @ {home_abbr}  —  {date_str}")
        print(f"  {away_pitcher} vs {home_pitcher}")
        print(f"{'─'*62}")

    # Look up from full-season Fangraphs cache
    away_p   = get_pitcher_stats(away_pitcher,  through_dt, year)
    home_p   = get_pitcher_stats(home_pitcher,  through_dt, year)
    away_wrc = get_team_wrc(away_abbr, through_dt, year)
    home_wrc = get_team_wrc(home_abbr, through_dt, year)
    away_bp  = get_team_bullpen_era(away_abbr, through_dt, year)
    home_bp  = get_team_bullpen_era(home_abbr, through_dt, year)

    # Fallback wRC+ if pull fails
    away_wrc = away_wrc or 100
    home_wrc = home_wrc or 100

    park = PARK_FACTORS.get(home_abbr, DEFAULT_PARK_FACTOR)

    # Check we have enough data
    def can_project(p):
        return p and p.get("siera") and p.get("xfip")

    if not can_project(away_p) or not can_project(home_p):
        if verbose:
            if not can_project(away_p): print(f"  ✗ Skipped — no SIERA/xFIP data for {away_pitcher}")
            if not can_project(home_p): print(f"  ✗ Skipped — no SIERA/xFIP data for {home_pitcher}")
        return None

    # ── Pitcher model ─────────────────────────────────────
    away_comp = composite_era(away_p["siera"], away_p["xfip"])
    home_comp = composite_era(home_p["siera"], home_p["xfip"])

    away_ip = avg_ip_per_start(away_p)
    home_ip = avg_ip_per_start(home_p)

    # Runs allowed by each starter (facing opposing offense)
    away_starter_ra = project_starter_runs(away_comp, home_wrc, park, away_ip)
    home_starter_ra = project_starter_runs(home_comp, away_wrc, park, home_ip)

    # Bullpen
    away_bp_runs = project_bullpen_runs(away_bp, away_ip)
    home_bp_runs = project_bullpen_runs(home_bp, home_ip)

    away_total_ra = round(away_starter_ra + away_bp_runs, 2)
    home_total_ra = round(home_starter_ra + home_bp_runs, 2)

    # ── Offense model ─────────────────────────────────────
    # Away team scores against home pitcher's runs allowed
    away_proj_scored = project_offense(away_wrc, home_total_ra, park)
    # Home team scores against away pitcher's runs allowed
    home_proj_scored = project_offense(home_wrc, away_total_ra, park)

    proj_total = round(away_proj_scored + home_proj_scored, 2)
    proj_spread = round(home_proj_scored - away_proj_scored, 2)

    # ── Actual score ──────────────────────────────────────
    if known_score:
        actual_away, actual_home = known_score
    else:
        actual_away, actual_home = get_actual_score(date_str, away_abbr, home_abbr)

    if verbose:
        print(f"\n  PITCHER MODEL:")
        print(f"    {away_pitcher:<22} SIERA {away_p['siera']:.2f} | xFIP {away_p['xfip']:.2f} → Composite {away_comp:.2f}")
        print(f"    {home_pitcher:<22} SIERA {home_p['siera']:.2f} | xFIP {home_p['xfip']:.2f} → Composite {home_comp:.2f}")
        print(f"\n  OFFENSE MODEL:")
        print(f"    {away_abbr} wRC+: {away_wrc:.0f} | {home_abbr} wRC+: {home_wrc:.0f} | Park factor: {park}")
        print(f"\n  PROJECTION:")
        print(f"    {away_abbr} runs scored: {away_proj_scored:.1f}  |  {home_abbr} runs scored: {home_proj_scored:.1f}")
        vegas_str = f" | Vegas: {vegas_total:.1f}" if vegas_total else ""
        print(f"    Projected total: {proj_total:.1f}{vegas_str}  |  Spread: {home_abbr} {proj_spread:+.1f}")
        if actual_away is not None:
            actual_total = actual_away + actual_home
            delta = round(proj_total - actual_total, 2)
            print(f"\n  RESULT:")
            print(f"    Actual:    {away_abbr} {actual_away} – {home_abbr} {actual_home}  (total: {actual_total})")
            print(f"    Projected: {away_abbr} {away_proj_scored:.1f} – {home_abbr} {home_proj_scored:.1f}  (total: {proj_total:.1f})")
            print(f"    Delta:     {delta:+.1f} runs  {'✅' if abs(delta) <= 1.5 else '⚠️ ' if abs(delta) <= 2.5 else '❌'}")
            if vegas_total:
                bet = simulate_bet(proj_total, vegas_total, actual_total, edge_threshold)
                if bet:
                    sym = "✅" if bet["outcome"] == "WIN" else ("⬜" if bet["outcome"] == "PUSH" else "❌")
                    print(f"    Vegas bet: Edge {bet['edge']:+.1f} → {bet['side']}  {sym} {bet['outcome']}"
                          f"  ({bet['units']:+.1f}u)")
                else:
                    edge_val = proj_total - vegas_total
                    print(f"    Vegas:     Edge {edge_val:+.1f} — below threshold, no bet")
        else:
            print(f"\n  ⚠  Actual score not available (game may not be in schedule data)")

    if actual_away is None:
        return None

    actual_total = actual_away + actual_home
    delta        = round(proj_total - actual_total, 2)
    bet          = simulate_bet(proj_total, vegas_total, actual_total, edge_threshold) if vegas_total else None

    return {
        "date":          date_str,
        "matchup":       f"{away_abbr} @ {home_abbr}",
        "away_pitcher":  away_pitcher,
        "home_pitcher":  home_pitcher,
        "away_siera":    away_p["siera"],
        "away_xfip":     away_p["xfip"],
        "away_comp_era": away_comp,
        "home_siera":    home_p["siera"],
        "home_xfip":     home_p["xfip"],
        "home_comp_era": home_comp,
        "away_wrc":      away_wrc,
        "home_wrc":      home_wrc,
        "park_factor":   park,
        "proj_away":     away_proj_scored,
        "proj_home":     home_proj_scored,
        "proj_total":    proj_total,
        "proj_spread":   proj_spread,
        "actual_away":   actual_away,
        "actual_home":   actual_home,
        "actual_total":  actual_total,
        "delta":         delta,
        "abs_delta":     abs(delta),
        "within_1_5":    abs(delta) <= 1.5,
        "within_2_5":    abs(delta) <= 2.5,
        "vegas_total":   vegas_total,
        "bet":           bet,
    }


# ── Results summary ───────────────────────────────────────────────────────────

def print_summary(results: List[Dict]) -> None:
    if not results:
        print("\n  No results to summarize.")
        return

    df = pd.DataFrame(results)

    mae   = df["abs_delta"].mean()
    rmse  = (df["delta"] ** 2).mean() ** 0.5
    w1_5  = df["within_1_5"].mean() * 100
    w2_5  = df["within_2_5"].mean() * 100
    bias  = df["delta"].mean()

    print(f"\n{'═'*62}")
    print(f"  BACKTEST SUMMARY — {len(results)} games")
    print(f"{'═'*62}")
    print(f"  Mean Absolute Error (MAE):       {mae:.2f} runs")
    print(f"  Root Mean Squared Error (RMSE):  {rmse:.2f} runs")
    print(f"  Projection bias (mean delta):    {bias:+.2f} runs  {'(over-projecting)' if bias > 0.3 else '(under-projecting)' if bias < -0.3 else '(no significant bias)'}")
    print(f"  Within 1.5 runs of actual:       {w1_5:.0f}%")
    print(f"  Within 2.5 runs of actual:       {w2_5:.0f}%")
    print(f"\n  GRADE:")
    if mae <= 1.5:
        print(f"  ✅ EXCELLENT  — MAE ≤ 1.5 runs. Model is tight.")
    elif mae <= 2.0:
        print(f"  ✅ GOOD       — MAE ≤ 2.0 runs. Solid baseline, weather/form will tighten it.")
    elif mae <= 2.5:
        print(f"  🟡 ACCEPTABLE — MAE ≤ 2.5 runs. Core is working, review outlier games.")
    else:
        print(f"  ❌ NEEDS WORK — MAE > 2.5 runs. Review formula weights.")

    # Outliers
    outliers = df[df["abs_delta"] > 3.0].sort_values("abs_delta", ascending=False)
    if not outliers.empty:
        print(f"\n  OUTLIERS (delta > 3.0 runs — investigate these):")
        for _, r in outliers.iterrows():
            print(f"    {r['date']} | {r['matchup']:<14} | Proj {r['proj_total']:.1f} | Actual {r['actual_total']} | Delta {r['delta']:+.1f}")

    # Park factor performance
    print(f"\n  BY PARK FACTOR:")
    df["park_bucket"] = pd.cut(df["park_factor"],
                                bins=[0, 95, 105, 200],
                                labels=["Pitcher (<95)", "Neutral (95-105)", "Hitter (>105)"])
    park_summary = df.groupby("park_bucket")["abs_delta"].agg(["mean", "count"])
    for bucket, row in park_summary.iterrows():
        print(f"    {str(bucket):<22}  MAE: {row['mean']:.2f}  ({int(row['count'])} games)")

    print(f"\n  RESULTS TABLE:")
    print(f"  {'Date':<12} {'Matchup':<14} {'Proj':>5} {'Actual':>7} {'Delta':>7}  Status")
    print(f"  {'─'*60}")
    for _, r in df.sort_values("abs_delta").iterrows():
        status = "✅" if r["within_1_5"] else ("🟡" if r["within_2_5"] else "❌")
        print(f"  {r['date']:<12} {r['matchup']:<14} {r['proj_total']:>5.1f} {r['actual_total']:>7} {r['delta']:>+7.1f}  {status}")

    print(f"\n{'═'*62}")


def save_results(results: List[Dict]) -> None:
    if not results:
        return
    df   = pd.DataFrame(results)
    fname = f"backtest_{date.today().isoformat()}.csv"
    df.to_csv(fname, index=False)
    print(f"\n  ✓ Results saved → {fname}")
    try:
        import openpyxl
        xlsx = fname.replace(".csv", ".xlsx")
        with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Results", index=False)
            # Summary row
            summary = pd.DataFrame([{
                "date": "SUMMARY",
                "matchup": f"{len(df)} games",
                "proj_total": df["proj_total"].mean(),
                "actual_total": df["actual_total"].mean(),
                "delta": df["delta"].mean(),
                "abs_delta": df["abs_delta"].mean(),
                "within_1_5": f"{df['within_1_5'].mean()*100:.0f}%",
                "within_2_5": f"{df['within_2_5'].mean()*100:.0f}%",
            }])
            summary.to_excel(writer, sheet_name="Summary", index=False)
        print(f"  ✓ Excel saved    → {xlsx}")
    except ImportError:
        pass


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SharpSportsPicks MLB Backtester")
    parser.add_argument("--n",        type=int,  default=40,    help="Games to sample (default: 40)")
    parser.add_argument("--quiet",    action="store_true",      help="Suppress per-game output")
    parser.add_argument("--season",   type=int,  default=2025,  help="Season year (default: 2025)")
    parser.add_argument("--seed",     type=int,  default=42,    help="Random seed")
    parser.add_argument("--edge",     type=float, default=1.5,  help="Min model-vs-Vegas edge to bet (default: 1.5)")
    parser.add_argument("--odds-csv", type=str,  default=None,  help="CSV with historical Vegas totals "
                                                                       "(date,away_team,home_team,close_total)")
    args = parser.parse_args()

    has_odds = (args.odds_csv is not None) or (2011 <= args.season <= 2021)

    print(f"\n{'═'*62}")
    print(f"  SharpSportsPicks AI — MLB Model Backtester")
    print(f"  Season: {args.season} | Target sample: {args.n} games")
    print(f"  Core model: Composite ERA = 0.60×SIERA + 0.40×xFIP")
    print(f"  Stats source: Full-season {args.season} Fangraphs (SIERA/xFIP/wRC+)")
    print(f"  Game data:   Real BR schedule (Win/Loss pitcher = starter proxy)")
    if has_odds:
        print(f"  Vegas odds:  {'Custom CSV' if args.odds_csv else f'SBR {args.season} archive'} | "
              f"Edge threshold: ≥{args.edge} runs")
    else:
        print(f"  Vegas odds:  Not available for {args.season} (use --season 2021 for free odds data)")
    print(f"  Note: weather, umpire, and form weights excluded (core only)")
    print(f"{'═'*62}")

    print(f"  Loading season stats...")
    _load_pitching(args.season)
    _load_batting(args.season)

    # Load Vegas odds (if available)
    odds_df = load_sbr_odds(args.season, args.odds_csv) if has_odds else pd.DataFrame()

    # Build real game list from actual schedule data
    games = build_real_games(args.season, n=args.n, seed=args.seed)

    if not games:
        print("  No games to run. Exiting.")
        return

    print(f"  Running {len(games)} games...\n")

    results = []
    skipped = 0

    for game_tuple, known_score in games:
        date_str, away_abbr, home_abbr, _, _ = game_tuple
        vt = get_vegas_total(date_str, away_abbr, home_abbr, odds_df) if not odds_df.empty else None
        result = run_game(game_tuple, verbose=not args.quiet,
                          known_score=known_score, vegas_total=vt,
                          edge_threshold=args.edge)
        if result:
            results.append(result)
        else:
            skipped += 1

    if skipped:
        print(f"\n  ⚠  {skipped} game(s) skipped — data unavailable or pitcher not found.")

    print_summary(results)
    if has_odds:
        print_betting_summary(results, args.edge)
    save_results(results)


if __name__ == "__main__":
    main()
