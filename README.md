# sleeper-api-analyzer

On-demand CLI that reviews your **National Gooning Association** Sleeper roster, scans the free-agent pool,
checks each player's real-life opponent for the week, and builds the **optimal starting lineup** (QB, RB, WR, TE, FLEX, K, DEF) for maximum points.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

League and user IDs are already set as defaults in `src/config.py`. To override, copy `.env.example` to `.env`.

## Run

```bash
python src/main.py                 # current NFL week
python src/main.py --week 6        # specific week
python src/main.py --show-all      # also list the top free agents per position
python src/main.py --refresh       # ignore cached API responses
python -m pytest                   # tests
```

### Single-position view

```bash
python src/position.py WR            # your WRs + the 5 best free-agent WRs, ranked by score (owner ignored)
python src/position.py RB --top 6    # only the 6 best of that list
python src/position.py WR --fa-only  # only free agents (top 5, or the N best with --top N)
python src/position.py FLEX          # WR/RB/TE together (also QB, TE, K, DEF)
```

Uses the same scoring and lineup logic as the full run. Each row shows owner (Mine/FA), tier, matchup,
projection, form, score and what the recommender does with that player (starter slot, ADD, DROP, bench, protected).
Prints to the terminal only.

Every full run prints to the terminal **and** writes `recommendations/week_<N>_recommendations.md` and `.json`.

## How it works

The tool reads your league's starting slots from Sleeper (`roster_positions`, falling back to 1 QB, 2 RB, 2 WR, 1 TE, 2 FLEX, 1 K, 1 DEF) and builds the best lineup from your roster + free agents:

### Phase 1: Lock Starters
- For each fixed slot (QB, RB, WR, TE, K, DEF), pick the best **available** player at that position.
- Unavailable players (bye/injured) are skipped during this phase.

### Phase 2: Fill FLEX Slots
- From remaining available players, fill each FLEX slot (WR/RB/TE; SUPER_FLEX also allows QB) with the best eligible player.
- Positions are compared against each other here, so any mix is possible.

### Phase 3: Build Bench
- Everyone else goes to the bench, sorted by score.
- **Protected players** (Elite or Mid tier unavailable) are kept here, never dropped.

### Phase 4: Recommend Moves
- Each free agent who made the lineup is paired with a rostered WR/RB/TE to drop (weakest value first).
- A free agent must beat the rostered player he displaces by that position's `swap_margin` (the larger `elite_swap_margin` for an available Elite player), otherwise no move. Both come from `tier_cutoffs.json`.
- If nobody can legally be dropped for a free agent, that free agent is not recommended.
- Output shows lineup score with your current roster vs after the moves.

## Scoring & Tiers

1. **Projections**: Sleeper's weekly projected stats, re-scored with *your league's* scoring settings.

2. **Matchup** (ESPN season-to-date stats, rank 1 = best defense):
   - QB / WR / TE: opponent **pass defense** (net passing yards allowed per game)
   - RB: opponent **run defense** (rushing yards allowed per game)
   - DEF: opponent **offense** (yards per game); K: neutral

3. **Composite Score** = 50% projection + 40% matchup-adjusted projection (±30% swing) + 10% recent form (3 games).

4. **Tier** (based on `search_rank`, a popularity proxy):
   - **Elite**: rank ≤ `elite_rank` → protected from drops if unavailable; free agents need the larger `elite_swap_margin` to displace
   - **Mid**: rank ≤ `mid_rank` → protected from drops if unavailable
   - **Bench**: below that → can drop if needed
   - All tier cutoffs **and swap margins** are in **`tier_cutoffs.json`** at the repo root, one explicit entry per position (QB, RB, WR, TE, K, DEF). Edit it and re-run; there are no hidden defaults in the code, and a missing or invalid entry stops the run with a clear message.

5. **Tier Protection**: Elite and Mid unavailable players are **never dropped**. They're kept on the bench for next week.
   Players in your IR/reserve slot are never touched, and only positions your lineup uses can be dropped.
   Free agents who are out/doubtful are never suggested.

Tier cutoffs and swap margins live in `tier_cutoffs.json`; score weights and other constants live in `src/config.py`.

## Output

Each run generates:
- **Terminal output**: Optimal lineup with recommended moves, tier, matchup, and composite score for each player.
- **Markdown report**: Week_<N>_recommendations.md with tables and summaries.
- **JSON**: week_<N>_recommendations.json with full player data and move details.

## Layout

```
tier_cutoffs.json   Elite/Mid rank cutoffs and swap margins per position (edit this)
src/   config.py  sleeper_client.py  nfl_data.py  player_tier.py  analyzer.py  recommender.py  report.py  main.py  position.py
tests/ unit tests + a fully mocked end-to-end run (no network needed)
.cache/            cached API responses (gitignored)
recommendations/   generated weekly reports
```

## Known limits

- ESPN stats are season-to-date, so early-season ranks are noisy.
- Only the ESPN team-statistics format is parsed defensively; if it changes, the tool warns and falls back to
  projection-only scoring instead of failing.
