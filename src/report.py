"""Format and export analysis results.

Provides rendering functions for terminal output, Markdown reports, and JSON export
of lineup recommendations and player evaluations.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import config
from analyzer import PlayerEval
from recommender import Result


def _fmt_trend(p: PlayerEval) -> str:
    return "-" if p.trend is None else f"{p.trend:.1f}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)] if rows else [len(h) for h in headers]
    line = lambda r: "  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip()
    return "\n".join([line(headers), line(["-" * w for w in widths])] + [line(r) for r in rows])


def _player_rows(players: list[PlayerEval]) -> list[list[str]]:
    return [
        [p.position, p.name, p.tier, p.matchup_note, f"{p.proj:.1f}", _fmt_trend(p), f"{p.score:.1f}"]
        for p in players
    ]


def _slot_rows(slots) -> list[list[str]]:
    rows = []
    for s in slots:
        if s.player:
            p = s.player
            tag = " (ADD)" if s.is_add else ""
            rows.append([s.position, p.name + tag, p.position, p.tier, p.matchup_note,
                         f"{p.proj:.1f}", _fmt_trend(p), f"{p.score:.1f}"])
        else:
            rows.append([s.position, "(empty - no eligible player)", "", "", "", "", "", ""])
    return rows


PLAYER_HEADERS = ["Pos", "Player", "Tier", "Matchup", "Proj", "Form", "Score"]
SLOT_HEADERS = ["Slot", "Player", "Pos", "Tier", "Matchup", "Proj", "Form", "Score"]


def _cutoff_label() -> str:
    return ", ".join(f"{pos} {c['elite_rank']}/{c['mid_rank']}" for pos, c in config.TIERS.items()
                     if c["elite_rank"] is not None)


def _margin_label() -> str:
    parts = []
    for pos, c in config.TIERS.items():
        em = c["elite_swap_margin"]
        parts.append(f"{pos} +{c['swap_margin']:g}" + (f" (Elite +{em:g})" if em is not None else ""))
    return ", ".join(parts)


def _lineup_label(slots: dict) -> str:
    order = list(config.FIXED_SLOT_ORDER) + list(config.FLEX_SLOTS)
    return ", ".join(f"{slots[k]} {k}" for k in order if slots.get(k))


def _summary(result: Result) -> str:
    return (f"Lineup score: {result.optimal_total:.1f} after moves vs {result.current_total:.1f} "
            f"with your current roster ({result.gain:+.1f})")


def render_text(result: Result, roster: list[PlayerEval], meta: dict, show_all: bool = False,
                free_agents: list[PlayerEval] | None = None) -> str:
    out = [
        f"{meta['league_name']} - Week {meta['week']} ({meta['season']})",
        "Lineup: " + _lineup_label(meta.get("slots") or config.LINEUP_SLOTS),
        "Tiers (Elite/Mid cutoffs, tier_cutoffs.json): " + _cutoff_label() + ". "
        "Free agent margins (tier_cutoffs.json): " + _margin_label(),
        "",
        f"RECOMMENDED MOVES ({len(result.moves)})",
    ]
    if not result.moves:
        out.append("  None - no free agent improves your lineup enough.")
    for i, m in enumerate(result.moves, 1):
        out.append(f"  {i}. DROP {m.drop.name} ({m.drop.position}, {m.drop.tier})  ->  ADD {m.add.name} "
                   f"({m.add.position}, {m.add.tier}) into {m.slot}")
        out.append(f"       - {m.reason}")
    out += ["", "STARTERS", _table(SLOT_HEADERS, _slot_rows(result.starters)),
            "", "FLEX", _table(SLOT_HEADERS, _slot_rows(result.flex)),
            "", f"BENCH ({len(result.bench)})"]
    out.append(_table(PLAYER_HEADERS, _player_rows(result.bench)) if result.bench else "  (empty)")
    if result.watch:
        out += ["", "INJURY / BYE WATCH (protected - kept on your roster)"]
        out += [f"  KEEP {p.name} ({p.tier}, {p.matchup_note})" for p in result.watch]
    out += ["", _summary(result)]
    out += [f"WARNING: {w}" for w in meta.get("warnings", [])]
    if show_all and free_agents is not None:
        out += ["", "TOP FREE AGENTS BY POSITION"]
        for pos in config.POSITIONS:
            top = sorted((f for f in free_agents if f.position == pos), key=lambda f: -f.score)[:5]
            if top:
                out.append(f"  {pos}")
                out += [f"    {f.name:<28} {f.matchup_note:<48} proj {f.proj:5.1f}  score {f.score:5.1f}" for f in top]
    return "\n".join(out)


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)] + \
           ["| " + " | ".join(r) + " |" for r in rows]


def render_markdown(result: Result, roster: list[PlayerEval], meta: dict) -> str:
    md = [
        f"# Week {meta['week']} Recommendations - {meta['league_name']}",
        f"_Generated {meta['generated_at']} - season {meta['season']}_",
        "",
        f"**{_summary(result)}**",
        "",
        f"## Recommended moves ({len(result.moves)})",
    ]
    if not result.moves:
        md.append("No free agent improves your lineup enough.")
    for m in result.moves:
        md.append(f"- **Drop {m.drop.name}** ({m.drop.position}, {m.drop.tier}) -> **add {m.add.name}** "
                  f"({m.add.position}, {m.add.tier}) into {m.slot}. {m.reason}")
    md += ["", "## Starters"] + _md_table(SLOT_HEADERS, _slot_rows(result.starters))
    md += ["", "## Flex"] + _md_table(SLOT_HEADERS, _slot_rows(result.flex))
    md += ["", f"## Bench ({len(result.bench)})"] + _md_table(PLAYER_HEADERS, _player_rows(result.bench))
    if result.watch:
        md += ["", "## Injury / bye watch (protected, kept)"]
        md += [f"- **{p.name}** ({p.tier}, {p.matchup_note})" for p in result.watch]
    md += [f"\n> WARNING: {w}" for w in meta.get("warnings", [])]
    return "\n".join(md) + "\n"


def _slot_json(slots) -> list[dict]:
    return [{"slot": s.position, "add": s.is_add, "player": s.player.to_dict() if s.player else None}
            for s in slots]


def to_json(result: Result, roster: list[PlayerEval], meta: dict) -> dict:
    return {
        "meta": meta,
        "rules": {
            "lineup_slots": meta.get("slots") or config.LINEUP_SLOTS,
            "tiers": config.TIERS,
            "weights": {"projection": config.W_PROJECTION, "matchup": config.W_MATCHUP, "trend": config.W_TREND},
        },
        "lineup_score": {"current": round(result.current_total, 2), "after_moves": round(result.optimal_total, 2),
                         "gain": round(result.gain, 2)},
        "moves": [{"drop": m.drop.to_dict(), "add": m.add.to_dict(), "slot": m.slot, "reason": m.reason}
                  for m in result.moves],
        "starters": _slot_json(result.starters),
        "flex": _slot_json(result.flex),
        "bench": [p.to_dict() for p in result.bench],
        "injury_bye_watch": [p.to_dict() for p in result.watch],
        "roster": [p.to_dict() for p in roster],
    }


def save_outputs(result: Result, roster: list[PlayerEval], meta: dict) -> tuple[Path, Path]:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    md_path = config.OUTPUT_DIR / f"week_{meta['week']}_recommendations.md"
    json_path = config.OUTPUT_DIR / f"week_{meta['week']}_recommendations.json"
    md_path.write_text(render_markdown(result, roster, meta))
    json_path.write_text(json.dumps(to_json(result, roster, meta), indent=2))
    return md_path, json_path


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")