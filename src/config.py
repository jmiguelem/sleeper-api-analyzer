"""Central configuration for analysis tool.

League IDs are hardcoded as defaults (single-league setup) but can be overridden
via .env file using SLEEPER_LEAGUE_ID and SLEEPER_USER_ID environment variables.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# Root directory of the project
ROOT = Path(__file__).resolve().parent.parent

# Load .env file if python-dotenv is available
if load_dotenv:
    load_dotenv(ROOT / ".env")

# ==============================================================================
# LEAGUE CONFIGURATION
# ==============================================================================

LEAGUE_ID = os.getenv("SLEEPER_LEAGUE_ID", "1395860624533094400")
USER_ID = os.getenv("SLEEPER_USER_ID", "1395864061605855232")
USERNAME = os.getenv("SLEEPER_USERNAME", "")

# ==============================================================================
# SCORING CONFIGURATION
# ==============================================================================

# Composite score weights (must sum to 1.0)
# score = 0.50 × projection + 0.40 × matchup + 0.10 × trend
W_PROJECTION = 0.50
W_MATCHUP = 0.40
W_TREND = 0.10

# Maximum swing (±) on projection due to matchup strength
# With W_MATCHUP = 0.40, this translates to max ±12% swing on final score
MATCHUP_SWING = 0.30

# Number of recent weeks to average for trend calculation
TREND_WEEKS = 3

# ==============================================================================
# TIER CONFIGURATION
# ==============================================================================
# All tier definitions are read from tier_cutoffs.json with explicit validation.
# No code defaults exist; missing entries will cause tool exit with clear error.

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


# Load tier configuration from tier_cutoffs.json
TIERS = load_tiers()
TIERED_POSITIONS = tuple(p for p in _ALL_POSITIONS if TIERS[p]["elite_rank"] is not None)


def elite_rank(position: str) -> int | None:
    """Get elite tier cutoff rank for a position."""
    return TIERS[position]["elite_rank"]


def mid_rank(position: str) -> int | None:
    """Get mid tier cutoff rank for a position."""
    return TIERS[position]["mid_rank"]


def swap_margin(position: str) -> float:
    """Get swap margin (points needed to justify roster swap) for a position."""
    return TIERS[position]["swap_margin"]


def elite_swap_margin(position: str) -> float | None:
    """Get elite swap margin for a position (higher threshold for Elite tier players)."""
    return TIERS[position]["elite_swap_margin"]


# ==============================================================================
# LINEUP CONFIGURATION
# ==============================================================================

# Default roster slots (overridden by league's actual roster_positions from Sleeper)
LINEUP_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DEF": 1, "FLEX": 2}

# Fixed position slots (filled first in lineup optimization)
FIXED_SLOT_ORDER = ("QB", "RB", "WR", "TE", "K", "DEF")

# Flexible slots and their eligible positions (filled second)
FLEX_SLOTS = {
    "REC_FLEX": ("WR", "TE"),
    "WRRB_FLEX": ("WR", "RB"),
    "FLEX": ("WR", "RB", "TE"),
    "SUPER_FLEX": ("QB", "WR", "RB", "TE"),
}

# Legacy compatibility
FLEX_ELIGIBLE = FLEX_SLOTS["FLEX"]


def slots_from_league(roster_positions) -> dict:
    """Parse league's roster format from Sleeper API.

    Counts starting slots from roster_positions (e.g. ["QB","RB","RB","WR",...,"BN"]).
    Ignores bench, IR, taxi, and IDP entries. Falls back to LINEUP_SLOTS if unusable.
    """
    counts: dict = {}
    for name in roster_positions or []:
        if name in FIXED_SLOT_ORDER or name in FLEX_SLOTS:
            counts[name] = counts.get(name, 0) + 1
    return counts or dict(LINEUP_SLOTS)


# All positions in the league
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")

# Player statuses indicating they will not play this week
OUT_STATUSES = {"Out", "IR", "PUP", "Sus", "NA", "Doubtful"}

# ==============================================================================
# PATHS AND HTTP CONFIGURATION
# ==============================================================================

CACHE_DIR = ROOT / ".cache"
OUTPUT_DIR = ROOT / "recommendations"
HTTP_TIMEOUT = 30
HTTP_RETRIES = 3

# ESPN team abbreviation mapping (ESPN uses different abbreviations than Sleeper)
ESPN_TO_SLEEPER = {"WSH": "WAS"}