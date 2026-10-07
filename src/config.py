"""Central configuration. League/user IDs are hardcoded defaults (single league),
overridable through a local .env file."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional; defaults below still work
    load_dotenv = None

ROOT = Path(__file__).resolve().parent.parent
if load_dotenv:
    load_dotenv(ROOT / ".env")

# --- League (National Gooning Association) -----------------------------------
LEAGUE_ID = os.getenv("SLEEPER_LEAGUE_ID", "1395860624533094400")
USER_ID = os.getenv("SLEEPER_USER_ID", "1395864061605855232")
USERNAME = os.getenv("SLEEPER_USERNAME", "mikelizalde")

# --- Recommendation rules ----------------------------------------------------
# Tier cutoffs and swap margins per position live in tier_cutoffs.json (no defaults here).

# Composite score weights (must sum to 1.0)
W_PROJECTION = 0.50
W_MATCHUP = 0.40
W_TREND = 0.10
# Max +/- swing a perfect/awful matchup applies to a projection (0.30 = +/-30%).
# With W_MATCHUP = 0.40 this moves the final score by at most +/-12%.
MATCHUP_SWING = 0.30
TREND_WEEKS = 3

# Tiering and swap margins per position: read from tier_cutoffs.json (repo root).
# Rank = Sleeper search_rank order within the position. See the "_help" entry in that file.
TIER_FILE = ROOT / "tier_cutoffs.json"
_ALL_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
_FIELDS = ("elite_rank", "mid_rank", "swap_margin", "elite_swap_margin")


def load_tiers(path=None) -> dict:
    """Read and validate tier_cutoffs.json -> {"WR": {"elite_rank": 20, ...}, ...}.
    Every position and field must be present; keys starting with "_" are notes. Raises ValueError
    naming the file and position for anything missing, unknown or invalid."""
    import json
    path = path or TIER_FILE
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError as exc:
        raise ValueError(f"{path} not found: it defines tier cutoffs and swap margins") from exc
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    data = {k: v for k, v in data.items() if not k.startswith("_")}
    unknown = set(data) - set(_ALL_POSITIONS)
    if unknown:
        raise ValueError(f"{path}: unknown position(s) {sorted(unknown)}; allowed: {list(_ALL_POSITIONS)}")
    tiers = {}
    for pos in _ALL_POSITIONS:
        if pos not in data:
            raise ValueError(f"{path}: position {pos} is missing")
        entry = data[pos]
        extra = set(entry) - set(_FIELDS)
        missing = set(_FIELDS) - set(entry)
        if extra or missing:
            raise ValueError(f"{path}: {pos} needs exactly {list(_FIELDS)} (missing {sorted(missing)}, unknown {sorted(extra)})")
        elite, mid, margin, elite_margin = (entry[f] for f in _FIELDS)
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
        if not (num(margin) and margin >= 0):
            raise ValueError(f"{path}: {pos}.swap_margin must be a number >= 0 (got {margin!r})")
        if pos in ("K", "DEF"):
            if (elite, mid, elite_margin) != (None, None, None):
                raise ValueError(f"{path}: {pos} has no tiers; set elite_rank, mid_rank and elite_swap_margin to null")
        else:
            if not (isinstance(elite, int) and isinstance(mid, int) and 0 < elite <= mid):
                raise ValueError(f"{path}: {pos} needs integer ranks with 0 < elite_rank <= mid_rank (got {elite!r}, {mid!r})")
            if not (num(elite_margin) and elite_margin >= margin):
                raise ValueError(f"{path}: {pos}.elite_swap_margin must be a number >= swap_margin (got {elite_margin!r})")
        tiers[pos] = {"elite_rank": elite, "mid_rank": mid, "swap_margin": margin, "elite_swap_margin": elite_margin}
    return tiers


TIERS = load_tiers()
TIERED_POSITIONS = tuple(p for p in _ALL_POSITIONS if TIERS[p]["elite_rank"] is not None)


def elite_rank(position: str):
    return TIERS[position]["elite_rank"]


def mid_rank(position: str):
    return TIERS[position]["mid_rank"]


def swap_margin(position: str) -> float:
    """Points a free agent must beat a rostered player at this position by."""
    return TIERS[position]["swap_margin"]


def elite_swap_margin(position: str):
    """Margin needed to replace an available Elite player (None for K/DEF: no tiers)."""
    return TIERS[position]["elite_swap_margin"]


# --- League format ---------------------------------------------------------------
# Starting slots. At runtime these are read from the league's own `roster_positions`
# (Sleeper API) via slots_from_league(); the values below are the fallback if that is missing.
LINEUP_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DEF": 1, "FLEX": 2}
FIXED_SLOT_ORDER = ("QB", "RB", "WR", "TE", "K", "DEF")
# Flex slot name -> positions allowed in it (filled most-restrictive first)
FLEX_SLOTS = {
    "REC_FLEX": ("WR", "TE"),
    "WRRB_FLEX": ("WR", "RB"),
    "FLEX": ("WR", "RB", "TE"),
    "SUPER_FLEX": ("QB", "WR", "RB", "TE"),
}
FLEX_ELIGIBLE = FLEX_SLOTS["FLEX"]  # kept for compatibility


def slots_from_league(roster_positions) -> dict:
    """Count starting slots from Sleeper's roster_positions (e.g. ["QB","RB","RB",...,"BN"]).
    Bench / IR / taxi / IDP entries are ignored. Falls back to LINEUP_SLOTS if nothing usable."""
    counts: dict = {}
    for name in roster_positions or []:
        if name in FIXED_SLOT_ORDER or name in FLEX_SLOTS:
            counts[name] = counts.get(name, 0) + 1
    return counts or dict(LINEUP_SLOTS)


POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
# Injury designations treated as "will not play this week"
OUT_STATUSES = {"Out", "IR", "PUP", "Sus", "NA", "Doubtful"}

# --- Paths / HTTP ------------------------------------------------------------
CACHE_DIR = ROOT / ".cache"
OUTPUT_DIR = ROOT / "recommendations"
HTTP_TIMEOUT = 30
HTTP_RETRIES = 3

# ESPN uses a few different team abbreviations than Sleeper
ESPN_TO_SLEEPER = {"WSH": "WAS"}
