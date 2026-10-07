"""Sleeper API access: league, rosters, players, projections, weekly stats."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

import requests

import config

API = "https://api.sleeper.app/v1"
API_DATA = "https://api.sleeper.com"  # projections / stats live on this host


class DataError(RuntimeError):
    """Raised when an upstream source returns unusable data."""


def http_get_json(url: str, params: Optional[dict] = None, session=None) -> Any:
    """GET with retries/backoff. Raises DataError after the last attempt."""
    sess = session or requests
    last: Exception | None = None
    for attempt in range(config.HTTP_RETRIES):
        try:
            resp = sess.get(url, params=params, timeout=config.HTTP_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise DataError(f"Request failed after {config.HTTP_RETRIES} attempts: {url} ({last})")


def cached(name: str, ttl: Optional[float], loader: Callable[[], Any], refresh: bool = False) -> Any:
    """Tiny JSON file cache. ttl=None means cache forever."""
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path: Path = config.CACHE_DIR / f"{name}.json"
    if not refresh and path.exists():
        age = time.time() - path.stat().st_mtime
        if ttl is None or age < ttl:
            try:
                return json.loads(path.read_text())
            except ValueError:
                pass
    data = loader()
    path.write_text(json.dumps(data))
    return data


def fantasy_points(stats: dict, scoring: dict) -> float:
    """Score a stat line with the league's own scoring settings."""
    return sum(float(stats[k]) * float(w) for k, w in scoring.items() if k in stats)


class SleeperClient:
    def __init__(self, league_id: str, user_id: str, refresh: bool = False, session=None):
        self.league_id = league_id
        self.user_id = str(user_id)
        self.refresh = refresh
        self.session = session

    def _get(self, url: str, params: Optional[dict] = None) -> Any:
        return http_get_json(url, params, self.session)

    # -- league -----------------------------------------------------------
    def get_state(self) -> dict:
        return self._get(f"{API}/state/nfl")

    def get_league(self) -> dict:
        return self._get(f"{API}/league/{self.league_id}")

    def get_rosters(self) -> list[dict]:
        return self._get(f"{API}/league/{self.league_id}/rosters")

    def get_my_roster(self, rosters: Optional[list[dict]] = None) -> dict:
        for r in rosters or self.get_rosters():
            if str(r.get("owner_id")) == self.user_id or self.user_id in map(str, r.get("co_owners") or []):
                return r
        raise DataError(f"No roster owned by user {self.user_id} in league {self.league_id}")

    # -- players ----------------------------------------------------------
    def get_players(self) -> dict[str, dict]:
        """Full NFL player database (~10MB), cached for 24h."""
        return cached("players_nfl", 24 * 3600, lambda: self._get(f"{API}/players/nfl"), self.refresh)

    # -- projections / stats ---------------------------------------------
    def get_projections(self, season: str, week: int) -> list[dict]:
        def load():
            return self._get(
                f"{API_DATA}/projections/nfl/{season}/{week}",
                params=[("season_type", "regular"), ("order_by", "pts_ppr")]
                + [("position[]", p) for p in config.POSITIONS],
            )
        return cached(f"proj_{season}_{week}", 6 * 3600, load, self.refresh)

    def get_week_stats(self, season: str, week: int) -> list[dict]:
        """Actual stats for a completed week (cached forever)."""
        def load():
            return self._get(
                f"{API_DATA}/stats/nfl/{season}/{week}",
                params=[("season_type", "regular")] + [("position[]", p) for p in config.POSITIONS],
            )
        return cached(f"stats_{season}_{week}", None, load, False)

    def recent_scores(self, season: str, week: int, scoring: dict) -> dict[str, float]:
        """Average league-scored points over the last TREND_WEEKS completed weeks,
        counting only weeks the player actually played. Missing weeks are skipped."""
        totals: dict[str, list[float]] = {}
        for w in range(max(1, week - config.TREND_WEEKS), week):
            try:
                rows = self.get_week_stats(season, w)
            except DataError:
                continue
            for row in rows:
                stats = row.get("stats") or {}
                if not stats.get("gp"):
                    continue
                totals.setdefault(str(row["player_id"]), []).append(fantasy_points(stats, scoring))
        return {pid: sum(v) / len(v) for pid, v in totals.items() if v}
