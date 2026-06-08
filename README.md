# World Cup 2026 Player Statistics

A data pipeline and analytics tool for the 2026 FIFA World Cup. Fetches per-match player statistics from the API-Football service, stores them in a DuckDB star schema, and provides a terminal reporting CLI.

---

## Prerequisites

- Python 3.10+
- An API key from [api-sports.io](https://api-sports.io) (free tier: 100 requests/day; paid tiers start at ~$10/month and cover full league seasons)

---

## Installation

```bash
# Clone and enter the repo
cd Playerstats

# Install dependencies
pip install -r requirements.txt

# Copy the example config and fill in your API key
cp .env.example .env
```

Edit `.env`:
```
API_FOOTBALL_KEY=your_api_key_here
DB_PATH=playerstats.db
LOG_LEVEL=INFO
```

---

## Data Pipeline (`pipeline.py`)

Run the pipeline steps in order. Each step is idempotent — safe to re-run.

### Step 1 — Initialise the database

Creates the DuckDB schema (tables, views) and populates the date dimension.

```bash
python pipeline.py init-db
```

### Step 2 — Fetch World Cup 2026 teams

Downloads the 32 national teams from the FIFA World Cup 2026 roster.

```bash
python pipeline.py fetch-teams
```

### Step 3 — Fetch squad rosters

Downloads the squad (player list) for every World Cup team loaded in step 2.

```bash
python pipeline.py fetch-squads
```

### Step 4 — Enrich players (club team + bio)

For each WC player, fetches their full profile for the given season: club team, nationality, height, weight, birth date.

```bash
python pipeline.py enrich-players
python pipeline.py enrich-players --season 2025   # default
```

**API call cost: 1 per WC player (~736 total).** On the free tier (100 calls/day) this step takes ~8 days, or upgrade to a paid plan.

### Step 5 — Fetch per-match player statistics

Now that we know each player's club, the pipeline fetches completed fixtures only for those clubs, then fetches per-match player stats for each fixture. Non-WC players are automatically excluded.

```bash
python pipeline.py fetch-wc-match-stats
python pipeline.py fetch-wc-match-stats --season 2025   # default
```

**API call cost: 1 per club (fixture list) + 1 per completed match.** Fixtures shared by two clubs (e.g. PSG vs Man City) are fetched only once.

### Run everything at once

```bash
python pipeline.py run-all --season 2025
```

---

## Reports (`report.py`)

### Player report

Search by name (partial, case-insensitive). Shows a match-by-match stats table and season totals.

```bash
python report.py player "Mbappe"
python report.py player "Haaland" --season 2025
```

If multiple players match the name, you will be prompted to pick one.

### Team report

Shows the World Cup squad with each player's aggregated season stats.

```bash
python report.py team "France"
python report.py team "Brazil" --season 2025
```

### Top players

Rank players by any stat. Defaults to World Cup 2026 squad players only.

```bash
python report.py top --stat goals
python report.py top --stat assists --position Midfielder --limit 10
python report.py top --stat rating --position Goalkeeper
python report.py top --stat tackles_total --all-players   # include non-WC players
```

Available stats: `assists`, `dribbles_success`, `goals`, `passes_key`, `passes_total`, `rating`, `saves`, `shots_on_target`, `shots_total`, `tackles_interceptions`, `tackles_total`

Available positions: `Goalkeeper`, `Defender`, `Midfielder`, `Attacker`

### Compare players

Side-by-side comparison of two or more players.

```bash
python report.py compare --players "Mbappe,Haaland"
python report.py compare --players "Salah,Son,Vinicius" --season 2025
```

---

## Database Schema

The data is stored in a local DuckDB file (`playerstats.db` by default).

```
Dimensions
  dim_date          — calendar dates (2024–2027)
  dim_competition   — leagues / tournaments (one row per league × season)
  dim_team          — club and national teams
  dim_player        — player bio (position, nationality, WC squad assignment)
  dim_match         — fixture metadata (teams, score, venue, round)

Fact table
  fact_player_match_stats
      player_id         → dim_player
      match_id          → dim_match
      date_key          → dim_date
      club_team_id      → dim_team   (club they played for in THIS match)
      opponent_team_id  → dim_team   (opposing team in THIS match)
      competition_id    → dim_competition
      national_team_id  → dim_team   (WC 2026 squad — for filtering by nation)
      + goals, assists, shots, passes, tackles, duels, dribbles,
        cards, penalties, saves, goals_conceded, rating, ...

Views
  v_player_season_summary   — season totals per player × competition
  v_world_cup_team_stats    — aggregate stats per World Cup nation
```

### Example SQL queries

```sql
-- All matches played by French World Cup players in season 2025
SELECT p.name, d.full_date, ct.name AS club, ot.name AS opponent,
       f.goals, f.assists, f.rating
FROM fact_player_match_stats f
JOIN dim_player p  ON f.player_id        = p.player_id
JOIN dim_team   ct ON f.club_team_id     = ct.team_id
JOIN dim_team   ot ON f.opponent_team_id = ot.team_id
JOIN dim_team   nt ON f.national_team_id = nt.team_id
JOIN dim_date   d  ON f.date_key         = d.date_key
WHERE nt.name = 'France'
ORDER BY d.full_date;

-- Top scorers across all World Cup squads
SELECT p.name, t.name AS national_team, SUM(f.goals) AS goals
FROM fact_player_match_stats f
JOIN dim_player p ON f.player_id        = p.player_id
JOIN dim_team   t ON f.national_team_id = t.team_id
GROUP BY p.name, t.name
ORDER BY goals DESC
LIMIT 20;
```

Open DuckDB directly:
```bash
python -c "import duckdb; con = duckdb.connect('playerstats.db'); con.sql('SELECT * FROM v_world_cup_team_stats').show()"
```
