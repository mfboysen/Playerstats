"""
ETL Load layer — upserts transformed rows into DuckDB.
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.db.database import Database

logger = logging.getLogger(__name__)


def _next_id(db: "Database", table: str, pk_col: str) -> int:
    """Return MAX(pk_col) + 1 from *table*, or 1 if the table is empty."""
    row = db.query_one(f"SELECT COALESCE(MAX({pk_col}), 0) + 1 AS next_id FROM {table}")
    return int(row["next_id"]) if row else 1


class Loader:
    """Loads transformed data into the DuckDB star schema."""

    def __init__(self, db: "Database"):
        """
        Args:
            db: Connected Database instance
        """
        self.db = db

    # ------------------------------------------------------------------
    # ID maps (api_id -> surrogate PK)
    # ------------------------------------------------------------------

    def get_team_id_map(self) -> dict:
        """Return {api_team_id: team_id} for all rows in dim_team."""
        df = self.db.query("SELECT api_team_id, team_id FROM dim_team")
        return {int(k): int(v) for k, v in zip(df["api_team_id"], df["team_id"])}

    def get_player_id_map(self) -> dict:
        """Return {api_player_id: player_id} for all rows in dim_player."""
        df = self.db.query("SELECT api_player_id, player_id FROM dim_player")
        return {int(k): int(v) for k, v in zip(df["api_player_id"], df["player_id"])}

    def get_match_id_map(self) -> dict:
        """Return {api_fixture_id: match_id} for all rows in dim_match."""
        df = self.db.query("SELECT api_fixture_id, match_id FROM dim_match")
        return {int(k): int(v) for k, v in zip(df["api_fixture_id"], df["match_id"])}

    def get_national_team_map(self) -> dict:
        """Return {api_player_id: national_team_id} for players with a WC team assigned."""
        df = self.db.query(
            "SELECT api_player_id, world_cup_team_id FROM dim_player WHERE world_cup_team_id IS NOT NULL"
        )
        return {int(row["api_player_id"]): int(row["world_cup_team_id"]) for _, row in df.iterrows()}

    def get_competition_id_map(self) -> dict:
        """Return {(api_league_id, season): competition_id} for all rows in dim_competition."""
        df = self.db.query(
            "SELECT api_league_id, season, competition_id FROM dim_competition"
        )
        return {
            (int(row["api_league_id"]), int(row["season"])): int(row["competition_id"])
            for _, row in df.iterrows()
        }

    # ------------------------------------------------------------------
    # Dimension loaders
    # ------------------------------------------------------------------

    def upsert_teams(self, teams: list[dict]) -> int:
        """
        Upsert dim_team rows.

        For new rows a surrogate PK is generated sequentially.

        Args:
            teams: Output of transform_teams()

        Returns:
            Number of rows processed
        """
        if not teams:
            logger.info("upsert_teams: no rows to process")
            return 0

        # Build a map of existing api_team_id -> team_id
        existing_map = self.get_team_id_map()
        next_id = max(existing_map.values(), default=0) + 1

        sql = """
            INSERT INTO dim_team
                (team_id, api_team_id, name, short_name, country, logo_url, is_world_cup_2026)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (api_team_id) DO UPDATE SET
                name               = excluded.name,
                short_name         = excluded.short_name,
                country            = excluded.country,
                logo_url           = excluded.logo_url,
                is_world_cup_2026  = is_world_cup_2026 OR excluded.is_world_cup_2026
        """

        rows = []
        for t in teams:
            api_id = t["api_team_id"]
            if api_id in existing_map:
                surrogate_id = existing_map[api_id]
            else:
                surrogate_id = next_id
                existing_map[api_id] = surrogate_id
                next_id += 1
            rows.append(
                (
                    surrogate_id,
                    api_id,
                    t["name"],
                    t.get("short_name"),
                    t.get("country"),
                    t.get("logo_url"),
                    bool(t.get("is_world_cup_2026", False)),
                )
            )

        self.db.executemany(sql, rows)
        logger.info("upsert_teams: processed %d rows", len(rows))
        return len(rows)

    def upsert_players(self, players: list[dict]) -> int:
        """
        Upsert dim_player rows.

        Args:
            players: Output of transform_players() or enrich_players_from_stats()

        Returns:
            Number of rows processed
        """
        if not players:
            logger.info("upsert_players: no rows to process")
            return 0

        existing_map = self.get_player_id_map()
        next_id = max(existing_map.values(), default=0) + 1

        sql = """
            INSERT INTO dim_player
                (player_id, api_player_id, name, firstname, lastname,
                 nationality, birth_date, age, height, weight,
                 position, photo_url, world_cup_team_id, club_team_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (api_player_id) DO UPDATE SET
                name               = excluded.name,
                firstname          = COALESCE(excluded.firstname, dim_player.firstname),
                lastname           = COALESCE(excluded.lastname, dim_player.lastname),
                nationality        = COALESCE(excluded.nationality, dim_player.nationality),
                birth_date         = COALESCE(excluded.birth_date, dim_player.birth_date),
                age                = COALESCE(excluded.age, dim_player.age),
                height             = COALESCE(excluded.height, dim_player.height),
                weight             = COALESCE(excluded.weight, dim_player.weight),
                position           = COALESCE(excluded.position, dim_player.position),
                photo_url          = COALESCE(excluded.photo_url, dim_player.photo_url),
                world_cup_team_id  = COALESCE(excluded.world_cup_team_id, dim_player.world_cup_team_id),
                club_team_id       = COALESCE(excluded.club_team_id, dim_player.club_team_id)
        """

        # Build api_team_id -> team_id map for resolving club_team_id
        team_id_map = self.get_team_id_map()

        rows = []
        for p in players:
            api_id = p["api_player_id"]
            if api_id in existing_map:
                surrogate_id = existing_map[api_id]
            else:
                surrogate_id = next_id
                existing_map[api_id] = surrogate_id
                next_id += 1
            wc_team_id = p.get("world_cup_team_id")
            if wc_team_id is not None:
                wc_team_id = int(wc_team_id)
            # Resolve club_api_team_id to DB surrogate
            club_api_id = p.get("club_api_team_id")
            club_team_id = team_id_map.get(club_api_id) if club_api_id else p.get("club_team_id")
            if club_team_id is not None:
                club_team_id = int(club_team_id)
            rows.append(
                (
                    surrogate_id,
                    api_id,
                    p["name"],
                    p.get("firstname"),
                    p.get("lastname"),
                    p.get("nationality"),
                    p.get("birth_date"),
                    p.get("age"),
                    p.get("height"),
                    p.get("weight"),
                    p.get("position"),
                    p.get("photo_url"),
                    wc_team_id,
                    club_team_id,
                )
            )

        self.db.executemany(sql, rows)
        logger.info("upsert_players: processed %d rows", len(rows))
        return len(rows)

    def upsert_competitions(self, competitions: list[dict]) -> int:
        """
        Upsert dim_competition rows.

        Args:
            competitions: Output of transform_competitions()

        Returns:
            Number of rows processed
        """
        if not competitions:
            logger.info("upsert_competitions: no rows to process")
            return 0

        existing_map = self.get_competition_id_map()
        next_id = max(existing_map.values(), default=0) + 1

        sql = """
            INSERT INTO dim_competition
                (competition_id, api_league_id, name, type, country, logo_url, season)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (api_league_id, season) DO UPDATE SET
                name     = excluded.name,
                type     = excluded.type,
                country  = excluded.country,
                logo_url = excluded.logo_url
        """

        rows = []
        for c in competitions:
            key = (int(c["api_league_id"]), int(c["season"]))
            if key in existing_map:
                surrogate_id = existing_map[key]
            else:
                surrogate_id = next_id
                existing_map[key] = surrogate_id
                next_id += 1
            rows.append(
                (
                    surrogate_id,
                    c["api_league_id"],
                    c["name"],
                    c.get("type"),
                    c.get("country"),
                    c.get("logo_url"),
                    c["season"],
                )
            )

        self.db.executemany(sql, rows)
        logger.info("upsert_competitions: processed %d rows", len(rows))
        return len(rows)

    def upsert_matches(self, matches: list[dict]) -> int:
        """
        Upsert dim_match rows.

        Args:
            matches: Output of transform_matches()

        Returns:
            Number of rows processed
        """
        if not matches:
            logger.info("upsert_matches: no rows to process")
            return 0

        existing_map = self.get_match_id_map()
        next_id = max(existing_map.values(), default=0) + 1

        sql = """
            INSERT INTO dim_match (
                match_id, api_fixture_id, date_key, competition_id,
                home_team_id, away_team_id, home_goals, away_goals,
                venue, city, round, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (api_fixture_id) DO UPDATE SET
                date_key       = excluded.date_key,
                competition_id = excluded.competition_id,
                home_team_id   = excluded.home_team_id,
                away_team_id   = excluded.away_team_id,
                home_goals     = excluded.home_goals,
                away_goals     = excluded.away_goals,
                venue          = excluded.venue,
                city           = excluded.city,
                round          = excluded.round,
                status         = excluded.status
        """

        rows = []
        for m in matches:
            api_id = m["api_fixture_id"]
            if api_id in existing_map:
                surrogate_id = existing_map[api_id]
            else:
                surrogate_id = next_id
                existing_map[api_id] = surrogate_id
                next_id += 1
            rows.append(
                (
                    surrogate_id,
                    api_id,
                    m.get("date_key"),
                    m.get("competition_id"),
                    m.get("home_team_id"),
                    m.get("away_team_id"),
                    m.get("home_goals"),
                    m.get("away_goals"),
                    m.get("venue"),
                    m.get("city"),
                    m.get("round"),
                    m.get("status"),
                )
            )

        self.db.executemany(sql, rows)
        logger.info("upsert_matches: processed %d rows", len(rows))
        return len(rows)

    def upsert_match_player_stats(self, stats: list[dict]) -> int:
        """
        Upsert fact_player_match_stats rows.

        Args:
            stats: Output of transform_match_player_stats()

        Returns:
            Number of rows processed
        """
        if not stats:
            logger.info("upsert_match_player_stats: no rows to process")
            return 0

        df = self.db.query(
            "SELECT stat_id, player_id, match_id FROM fact_player_match_stats"
        )
        existing_map: dict[tuple, int] = {}
        for _, row in df.iterrows():
            key = (int(row["player_id"]), int(row["match_id"]))
            existing_map[key] = int(row["stat_id"])

        next_id = max(existing_map.values(), default=0) + 1

        sql = """
            INSERT INTO fact_player_match_stats (
                stat_id, player_id, match_id, date_key,
                club_team_id, opponent_team_id, competition_id, national_team_id,
                minutes_played, is_starter,
                goals, assists, shots_total, shots_on_target, offsides,
                passes_total, passes_key, pass_accuracy,
                tackles_total, tackles_blocks, tackles_interceptions,
                duels_total, duels_won,
                dribbles_attempts, dribbles_success, dribbles_past,
                fouls_drawn, fouls_committed,
                yellow_cards, red_cards,
                penalty_won, penalty_committed, penalty_scored, penalty_missed, penalty_saved,
                saves, goals_conceded,
                rating, updated_at
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?
            )
            ON CONFLICT (player_id, match_id) DO UPDATE SET
                date_key                = excluded.date_key,
                club_team_id            = excluded.club_team_id,
                opponent_team_id        = excluded.opponent_team_id,
                competition_id          = excluded.competition_id,
                national_team_id        = excluded.national_team_id,
                minutes_played          = excluded.minutes_played,
                is_starter              = excluded.is_starter,
                goals                   = excluded.goals,
                assists                 = excluded.assists,
                shots_total             = excluded.shots_total,
                shots_on_target         = excluded.shots_on_target,
                offsides                = excluded.offsides,
                passes_total            = excluded.passes_total,
                passes_key              = excluded.passes_key,
                pass_accuracy           = excluded.pass_accuracy,
                tackles_total           = excluded.tackles_total,
                tackles_blocks          = excluded.tackles_blocks,
                tackles_interceptions   = excluded.tackles_interceptions,
                duels_total             = excluded.duels_total,
                duels_won               = excluded.duels_won,
                dribbles_attempts       = excluded.dribbles_attempts,
                dribbles_success        = excluded.dribbles_success,
                dribbles_past           = excluded.dribbles_past,
                fouls_drawn             = excluded.fouls_drawn,
                fouls_committed         = excluded.fouls_committed,
                yellow_cards            = excluded.yellow_cards,
                red_cards               = excluded.red_cards,
                penalty_won             = excluded.penalty_won,
                penalty_committed       = excluded.penalty_committed,
                penalty_scored          = excluded.penalty_scored,
                penalty_missed          = excluded.penalty_missed,
                penalty_saved           = excluded.penalty_saved,
                saves                   = excluded.saves,
                goals_conceded          = excluded.goals_conceded,
                rating                  = excluded.rating,
                updated_at              = excluded.updated_at
        """

        rows = []
        for s in stats:
            key = (s["player_id"], s["match_id"])
            if key in existing_map:
                stat_id = existing_map[key]
            else:
                stat_id = next_id
                existing_map[key] = stat_id
                next_id += 1
            rows.append(
                (
                    stat_id,
                    s["player_id"],
                    s["match_id"],
                    s.get("date_key"),
                    s["club_team_id"],
                    s["opponent_team_id"],
                    s.get("competition_id"),
                    s.get("national_team_id"),
                    s.get("minutes_played", 0),
                    bool(s.get("is_starter", True)),
                    s.get("goals", 0),
                    s.get("assists", 0),
                    s.get("shots_total", 0),
                    s.get("shots_on_target", 0),
                    s.get("offsides", 0),
                    s.get("passes_total", 0),
                    s.get("passes_key", 0),
                    s.get("pass_accuracy"),
                    s.get("tackles_total", 0),
                    s.get("tackles_blocks", 0),
                    s.get("tackles_interceptions", 0),
                    s.get("duels_total", 0),
                    s.get("duels_won", 0),
                    s.get("dribbles_attempts", 0),
                    s.get("dribbles_success", 0),
                    s.get("dribbles_past", 0),
                    s.get("fouls_drawn", 0),
                    s.get("fouls_committed", 0),
                    s.get("yellow_cards", 0),
                    s.get("red_cards", 0),
                    s.get("penalty_won", 0),
                    s.get("penalty_committed", 0),
                    s.get("penalty_scored", 0),
                    s.get("penalty_missed", 0),
                    s.get("penalty_saved", 0),
                    s.get("saves", 0),
                    s.get("goals_conceded", 0),
                    s.get("rating"),
                    s.get("updated_at", datetime.now(timezone.utc).isoformat()),
                )
            )

        self.db.executemany(sql, rows)
        logger.info("upsert_match_player_stats: processed %d rows", len(rows))
        return len(rows)

    def load_date_dimension(self, dates: list[dict]) -> int:
        """
        Load the date dimension, skipping existing rows.

        Args:
            dates: Output of generate_date_dimension()

        Returns:
            Number of rows processed
        """
        if not dates:
            logger.info("load_date_dimension: no rows to process")
            return 0

        sql = """
            INSERT INTO dim_date
                (date_key, full_date, year, month, month_name, quarter,
                 week_of_year, day_of_week, day_name, is_weekend)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (date_key) DO NOTHING
        """

        rows = [
            (
                d["date_key"],
                d["full_date"],
                d["year"],
                d["month"],
                d["month_name"],
                d["quarter"],
                d["week_of_year"],
                d["day_of_week"],
                d["day_name"],
                d["is_weekend"],
            )
            for d in dates
        ]

        self.db.executemany(sql, rows)
        logger.info("load_date_dimension: processed %d rows", len(rows))
        return len(rows)
