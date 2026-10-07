# sleeper-api-analyzer

Fantasy football lineup optimizer using Sleeper API and ESPN matchup data. Analyzes your roster, identifies optimal weekly lineups, and recommends player swaps based on real-time opponent matchups.

## League-Specific Configuration

This tool is **configured for a specific Sleeper league**. The following are hardcoded:

| Aspect | Details |
|--------|---------|
| **League Setup** | Single league with ID in `src/config.py` |
| **Roster Format** | 1 QB, 2 RB, 2 WR, 1 TE, 2 FLEX, 1 K, 1 DEF |
| **Scoring Rules** | Uses exact scoring settings from Sleeper league |
| **Tier Cutoffs** | Customized per position in `tier_cutoffs.json` |

To adapt for another league, modify:
1. `LEAGUE_ID` and `USER_ID` in `src/config.py` (or set via `.env`)
2. Tier ranks and swap margins in `tier_cutoffs.json`
3. Verify roster format matches your league's `roster_positions`

## Setup

```bash
# Create virtual environment and install dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Configuration can be overridden by creating `.env` with custom `LEAGUE_ID` and `USER_ID`.

## Usage

### Full Lineup Analysis

```bash
python src/main.py                 # Analyze current NFL week
python src/main.py --week 6        # Analyze specific week
python src/main.py --show-all      # Include top 5 free agents per position
python src/main.py --refresh       # Skip cache, fetch fresh data
```

### Position-Specific Analysis

```bash
python src/position.py WR                # Your WRs plus top 5 free-agent WRs
python src/position.py RB --top 6        # Limit results to top 6
python src/position.py WR --fa-only      # Show only free agents
python src/position.py FLEX              # View WR/RB/TE together
```

Supported positions: `QB`, `RB`, `WR`, `TE`, `K`, `DEF`, `FLEX`

`src/position.py` ranks a single position using the same scoring and lineup logic as `main.py`. It's useful for a quick look at one spot in your lineup without the full report. It prints a table to the terminal only and doesn't write any files to `recommendations/`.

#### Options

| Option | Description |
|--------|-------------|
| `POSITION` | Required. One of `QB`, `RB`, `WR`, `TE`, `K`, `DEF`, `FLEX` (case-insensitive). `FLEX` includes WR, RB, and TE |
| `--top N` | Show only the N best players from the list. With `--fa-only`, show the N best free agents instead of the default 5 |
| `--fa-only` | Hide your rostered players and show only free agents |
| `--week N` | NFL week to analyze (default: current week) |
| `--refresh` | Ignore cached API responses and fetch fresh data |

#### What's listed

The list holds every player on your roster at that position, plus the top 5 available free agents. Free agents who are on bye, or listed as Out, Doubtful, IR, PUP, suspended, or NA, are left out. Everyone is sorted by score together, whether they're yours or a free agent, so you can compare them side by side.

#### Columns

| Column | Meaning |
|--------|---------|
| `#` | Rank by score within this list |
| `Player` | Player name |
| `Owner` | `Mine` (on your roster) or `FA` (free agent) |
| `Pos` | Player position |
| `Tier` | Elite / Mid / Bench, from `tier_cutoffs.json` |
| `Rank` | Positional rank (`-` if unranked) |
| `Matchup` | Opponent and defense-rank note behind the matchup adjustment, prefixed with injury status if any (`BYE` on bye weeks) |
| `Proj` | League-scored weekly projection |
| `Form` | Average points over the last 3 weeks (`-` if no data) |
| `Score` | Final blended score (see [Scoring Methodology](#scoring-methodology)) |
| `Status` | What the full recommender does with this player (see below) |

#### Status values

| Status | Meaning |
|--------|---------|
| `Starter: <SLOT>` | Rostered player in the optimal lineup at that slot |
| `ADD to <SLOT>` | Free agent the recommender would pick up for that slot |
| `DROP (for <name>)` | Rostered player the recommender would drop for that free agent |
| `Bench` | Rostered player not in the optimal lineup |
| `Bench - protected, out this week` | Unavailable Elite/Mid player who won't be dropped |
| `Reserve / IR` | Player in an IR/reserve slot (never touched) |
| `Free agent` | Free agent not recommended for pickup |

> **Note:** A free agent can rank above one of your players and still show `Free agent`. That's because the recommender only suggests a swap when the score gap is at least the position's `swap_margin`, or `elite_swap_margin` for Elite players. The ranking itself doesn't use the margin.

If the data can't be loaded, the script prints `ERROR: ...` to stderr and exits with code 1. Any data warnings, such as missing ESPN stats, are printed below the table.

### Running Tests

```bash
python -m pytest
```

## Output

Each analysis run generates two outputs:

| Format | File | Contents |
|--------|------|----------|
| Terminal | stdout | Formatted table with optimal lineup and recommended moves |
| Markdown | `recommendations/week_<N>_recommendations.md` | Detailed report with moves, tiers, and matchups |

## Algorithm

The tool reads your league's roster format from Sleeper and applies a four-phase lineup optimization algorithm:

### Phase 1: Lock Starters

For each fixed position (QB, RB, WR, TE, K, DEF), select the best available player at that position. Players marked as out, injured, or on bye are excluded.

### Phase 2: Fill FLEX Slots

Fill FLEX slots with the highest-scoring remaining available players. FLEX slots compare players cross-position (WR, RB, TE eligible; SUPER_FLEX also allows QB), allowing optimal allocation regardless of position scarcity.

### Phase 3: Build Bench

All remaining rostered players go to the bench, sorted by score. Elite and Mid-tier unavailable players are protected here and never recommended for drop.

### Phase 4: Recommend Moves

For each free agent in the optimal lineup, identify the lowest-value rostered player available for drop. A swap is recommended only if the free agent's score exceeds the rostered player's by at least the position's `swap_margin` (or `elite_swap_margin` for available Elite players). Output compares lineup score before and after proposed moves.

## Scoring Methodology

### Player Scoring

Player scores are computed from three components:

1. **Projection** (50%)
   - Sleeper's weekly projected stats, re-scored using your league's exact scoring settings

2. **Matchup Strength** (40%)
   - Opponent pass defense rank for QB/WR/TE (net passing yards allowed per game)
   - Opponent run defense rank for RB (rushing yards allowed per game)
   - Opponent overall offense rank for DEF (total yards per game)
   - Kicker positions are not adjusted for matchup
   - Adjustment range: ±30% swing on projection

3. **Recent Form** (10%)
   - Average scoring over the last 3 weeks
   - Provides volatility adjustment for trending players

**Formula:** `score = 0.50 × projection + 0.40 × matchup_adjusted_projection + 0.10 × trend`

### Player Tiers

Tiers are assigned based on `search_rank`, a player popularity metric from Sleeper:

| Tier | Criteria | Protection | Swap Margin |
|------|----------|-----------|-------------|
| Elite | rank ≤ elite_rank | Protected if unavailable | `elite_swap_margin` |
| Mid | rank ≤ mid_rank | Protected if unavailable | `swap_margin` |
| Bench | rank > mid_rank | Not protected | `swap_margin` |

All tier definitions are **explicitly configured in `tier_cutoffs.json`** with no code defaults. Each position has:
- `elite_rank` / `mid_rank` (or null for K/DEF)
- `swap_margin` (points needed to justify dropping a rostered player)
- `elite_swap_margin` (higher threshold for Elite players)

Invalid or missing entries in `tier_cutoffs.json` will cause the tool to exit with a clear error message.

### Tier Protection Rules

- Elite and Mid-tier players marked unavailable (out, injured, bye) are never dropped
- Players in IR/reserve slots are never touched
- Only positions used in your lineup can be dropped
- Free agents on bye or listed as Out, Doubtful, IR, PUP, suspended, or NA are never recommended for pickup

## Project Structure

```
tier_cutoffs.json              Configuration: tier ranks and swap margins per position
src/
  ├── config.py               Central configuration (league IDs, scoring weights, paths)
  ├── sleeper_client.py       Sleeper API client with response caching
  ├── nfl_data.py             ESPN team statistics and matchup data
  ├── player_tier.py          Player tier classification logic
  ├── analyzer.py             Player evaluation and scoring
  ├── recommender.py          Lineup optimization algorithm
  ├── report.py               Output formatting (terminal, markdown)
  ├── main.py                 Primary analysis entry point
  └── position.py             Per-position analysis script
tests/                         Unit tests + full end-to-end mock run
.cache/                        Cached API responses (gitignored)
recommendations/               Generated weekly reports
```

## Limitations

- **ESPN stats noise**: Season-to-date offensive/defensive rankings are unreliable early in the season
- **Format resilience**: ESPN's data structure is monitored. If the format changes, the tool logs a warning and falls back to projection-only scoring