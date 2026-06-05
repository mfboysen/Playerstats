"""
Analytics query layer — wraps common analytical queries over the DuckDB star schema.
"""

import logging
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from src.db.database import Database

logger = logging.getLogger(__name__)

# Columns available for top_players ranking
VALID_STAT_COLUMNS = {
    "goals",
    "assists",
    "passes_total",
    "passes_key",
    "tackles_total",
    "tackles_interceptions",
    "dribbles_success",
    "rating",
    "saves",
}


class Analytics:
    """High-level analytical queries over the World Cup player stats database."""

    def __init__(self, db: "Database"):
        """
        Args:
            db: Connected Database instance
        """
        self.db = db

    # ------------------------------------------------------------------
    # Player search / report
    # ------------------------------------------------------------------

    def search_player(self, name: str, season: int = 2025) -> pd.DataFrame:
        """
        Search for players by name (case-insensitive partial match).

        Returns a DataFrame with player info joined with aggregated season stats.

        Args:
            name: Partial player name to search for
            season: Season year for stat aggregation

        Returns:
            DataFrame with columns: api_player_id, player_name, nationality,
            position, world_cup_team, goals, assists, appearances, avg_rating
        """
        sql = """
            SELECT
                p.api_player_id,
                p.name              AS player_name,
                p.nationality,
                p.position,
                t_wc.name           AS world_cup_team,
                SUM(f.goals)        AS goals,
                SUM(f.assists)      AS assists,
                SUM(f.appearances)  AS appearances,
                SUM(f.minutes)      AS minutes,
                AVG(f.rating)       AS avg_rating
            FROM dim_player p
            LEFT JOIN fact_player_season_stats f
                   ON p.player_id = f.player_id
            LEFT JOIN dim_competition c
                   ON f.competition_id = c.competition_id
                  AND c.season = ?
            LEFT JOIN dim_team t_wc
                   ON p.world_cup_team_id = t_wc.team_id
            WHERE LOWER(p.name) LIKE LOWER(?)
            GROUP BY
                p.api_player_id, p.name, p.nationality,
                p.position, t_wc.name
            ORDER BY SUM(f.goals) DESC NULLS LAST, p.name
        """
        pattern = f"%{name}%"
        df = self.db.query(sql, [season, pattern])
        logger.info(
            "search_player('%s', season=%d): %d rows returned", name, season, len(df)
        )
        return df

    def player_report(self, api_player_id: int, season: int = 2025) -> dict:
        """
        Detailed report for a single player.

        Args:
            api_player_id: API player ID
            season: Season year

        Returns:
            dict with keys:
                'player'             : dict of dim_player info
                'stats_by_competition': DataFrame — one row per (team, competition)
                'totals'             : dict of summed / averaged stats across all clubs
        """
        # Player bio
        player = self.db.query_one(
            """
            SELECT
                p.api_player_id,
                p.name, p.firstname, p.lastname,
                p.nationality, p.birth_date, p.age,
                p.height, p.weight, p.position, p.photo_url,
                t_wc.name AS world_cup_team
            FROM dim_player p
            LEFT JOIN dim_team t_wc ON p.world_cup_team_id = t_wc.team_id
            WHERE p.api_player_id = ?
            """,
            [api_player_id],
        )

        if player is None:
            logger.warning("player_report: api_player_id=%d not found", api_player_id)
            return {"player": None, "stats_by_competition": pd.DataFrame(), "totals": {}}

        # Per-competition stats
        stats_df = self.db.query(
            """
            SELECT
                t.name                   AS club,
                c.name                   AS competition,
                c.country                AS competition_country,
                c.season,
                f.appearances,
                f.lineups,
                f.minutes,
                f.goals,
                f.assists,
                f.shots_total,
                f.shots_on_target,
                f.passes_total,
                f.passes_key,
                f.pass_accuracy,
                f.tackles_total,
                f.tackles_interceptions,
                f.tackles_blocks,
                f.duels_total,
                f.duels_won,
                f.dribbles_attempts,
                f.dribbles_success,
                f.fouls_committed,
                f.fouls_drawn,
                f.yellow_cards,
                f.red_cards,
                f.penalty_scored,
                f.saves,
                f.goals_conceded,
                f.rating
            FROM fact_player_season_stats f
            JOIN dim_player      p ON f.player_id      = p.player_id
            JOIN dim_team        t ON f.team_id         = t.team_id
            JOIN dim_competition c ON f.competition_id = c.competition_id
            WHERE p.api_player_id = ?
              AND c.season        = ?
            ORDER BY f.appearances DESC
            """,
            [api_player_id, season],
        )

        # Aggregate totals
        totals: dict = {}
        if not stats_df.empty:
            num_cols = [
                "appearances", "lineups", "minutes", "goals", "assists",
                "shots_total", "shots_on_target", "passes_total", "passes_key",
                "tackles_total", "tackles_interceptions", "tackles_blocks",
                "duels_total", "duels_won", "dribbles_attempts", "dribbles_success",
                "fouls_committed", "fouls_drawn", "yellow_cards", "red_cards",
                "penalty_scored", "saves", "goals_conceded",
            ]
            for col in num_cols:
                if col in stats_df.columns:
                    totals[col] = int(stats_df[col].sum())
            if "rating" in stats_df.columns:
                totals["avg_rating"] = round(float(stats_df["rating"].mean()), 2)
            if "pass_accuracy" in stats_df.columns:
                totals["avg_pass_accuracy"] = round(
                    float(stats_df["pass_accuracy"].mean()), 2
                )

        return {
            "player": player,
            "stats_by_competition": stats_df,
            "totals": totals,
        }

    # ------------------------------------------------------------------
    # Team search / report
    # ------------------------------------------------------------------

    def search_team(self, name: str, season: int = 2025) -> pd.DataFrame:
        """
        Search World Cup teams by name (case-insensitive partial match).

        Args:
            name: Partial team name
            season: (unused, kept for API consistency)

        Returns:
            DataFrame with basic team info
        """
        sql = """
            SELECT
                t.api_team_id,
                t.name,
                t.country,
                t.logo_url,
                t.is_world_cup_2026
            FROM dim_team t
            WHERE t.is_world_cup_2026 = TRUE
              AND LOWER(t.name) LIKE LOWER(?)
            ORDER BY t.name
        """
        df = self.db.query(sql, [f"%{name}%"])
        logger.info("search_team('%s'): %d rows returned", name, len(df))
        return df

    def team_report(self, api_team_id: int, season: int = 2025) -> dict:
        """
        Detailed report for a World Cup team.

        Args:
            api_team_id: API team ID (World Cup team)
            season: Club season for player stats

        Returns:
            dict with keys:
                'team'        : dict of dim_team info
                'squad_stats' : DataFrame — one row per player, sorted by rating desc
        """
        team = self.db.query_one(
            "SELECT * FROM dim_team WHERE api_team_id = ?", [api_team_id]
        )
        if team is None:
            logger.warning("team_report: api_team_id=%d not found", api_team_id)
            return {"team": None, "squad_stats": pd.DataFrame()}

        squad_df = self.db.query(
            """
            SELECT
                p.api_player_id,
                p.name          AS player_name,
                p.position,
                p.age,
                p.nationality,
                SUM(f.appearances)          AS appearances,
                SUM(f.minutes)              AS minutes,
                SUM(f.goals)                AS goals,
                SUM(f.assists)              AS assists,
                SUM(f.shots_total)          AS shots_total,
                SUM(f.passes_total)         AS passes_total,
                SUM(f.passes_key)           AS key_passes,
                SUM(f.tackles_total)        AS tackles,
                SUM(f.tackles_interceptions) AS interceptions,
                SUM(f.yellow_cards)         AS yellow_cards,
                SUM(f.red_cards)            AS red_cards,
                AVG(f.rating)               AS avg_rating
            FROM dim_player p
            JOIN dim_team t_wc
                ON p.world_cup_team_id = t_wc.team_id
            LEFT JOIN fact_player_season_stats f
                ON p.player_id = f.player_id
            LEFT JOIN dim_competition c
                ON f.competition_id = c.competition_id
               AND c.season = ?
            WHERE t_wc.api_team_id = ?
            GROUP BY
                p.api_player_id, p.name, p.position, p.age, p.nationality
            ORDER BY AVG(f.rating) DESC NULLS LAST, SUM(f.goals) DESC NULLS LAST
            """,
            [season, api_team_id],
        )

        return {"team": team, "squad_stats": squad_df}

    # ------------------------------------------------------------------
    # Top players
    # ------------------------------------------------------------------

    def top_players(
        self,
        stat: str,
        season: int = 2025,
        position: str = None,
        limit: int = 20,
        world_cup_only: bool = True,
    ) -> pd.DataFrame:
        """
        Rank players by a given statistic.

        Args:
            stat: Column name to rank by (must be in VALID_STAT_COLUMNS)
            season: Club season year
            position: Optional position filter (Goalkeeper / Defender /
                      Midfielder / Attacker)
            limit: Number of rows to return
            world_cup_only: If True, only include World Cup 2026 squad players

        Returns:
            DataFrame of top players sorted descending by *stat*

        Raises:
            ValueError: If *stat* is not in VALID_STAT_COLUMNS
        """
        if stat not in VALID_STAT_COLUMNS:
            raise ValueError(
                f"Invalid stat '{stat}'. Valid options: {sorted(VALID_STAT_COLUMNS)}"
            )

        # Decide aggregation function
        avg_stats = {"rating"}
        if stat in avg_stats:
            agg_expr = f"AVG(f.{stat})"
        else:
            agg_expr = f"SUM(f.{stat})"

        wc_filter = "AND p.world_cup_team_id IS NOT NULL" if world_cup_only else ""
        pos_filter = "AND p.position = ?" if position else ""

        sql = f"""
            SELECT
                p.api_player_id,
                p.name          AS player_name,
                p.nationality,
                p.position,
                t_wc.name       AS world_cup_team,
                {agg_expr}      AS {stat},
                SUM(f.goals)    AS goals,
                SUM(f.assists)  AS assists,
                SUM(f.appearances) AS appearances,
                SUM(f.minutes)  AS minutes,
                AVG(f.rating)   AS avg_rating
            FROM dim_player p
            LEFT JOIN dim_team t_wc
                ON p.world_cup_team_id = t_wc.team_id
            JOIN fact_player_season_stats f
                ON p.player_id = f.player_id
            JOIN dim_competition c
                ON f.competition_id = c.competition_id
            WHERE c.season = ?
              {wc_filter}
              {pos_filter}
            GROUP BY
                p.api_player_id, p.name, p.nationality,
                p.position, t_wc.name
            HAVING {agg_expr} IS NOT NULL
            ORDER BY {agg_expr} DESC
            LIMIT ?
        """

        params: list = [season]
        if position:
            params.append(position)
        params.append(limit)

        df = self.db.query(sql, params)
        df.insert(0, "rank", range(1, len(df) + 1))
        logger.info(
            "top_players(stat=%s, season=%d, position=%s, wc_only=%s): %d rows",
            stat,
            season,
            position,
            world_cup_only,
            len(df),
        )
        return df

    # ------------------------------------------------------------------
    # Compare players
    # ------------------------------------------------------------------

    def compare_players(
        self, player_ids: list[int], season: int = 2025
    ) -> pd.DataFrame:
        """
        Side-by-side comparison of multiple players.

        Args:
            player_ids: List of API player IDs to compare
            season: Club season year

        Returns:
            DataFrame with one row per player, key stats as columns
        """
        if not player_ids:
            return pd.DataFrame()

        placeholders = ", ".join(["?"] * len(player_ids))

        sql = f"""
            SELECT
                p.api_player_id,
                p.name              AS player_name,
                p.nationality,
                p.position,
                p.age,
                t_wc.name           AS world_cup_team,
                SUM(f.appearances)  AS appearances,
                SUM(f.minutes)      AS minutes,
                SUM(f.goals)        AS goals,
                SUM(f.assists)      AS assists,
                SUM(f.shots_total)          AS shots_total,
                SUM(f.shots_on_target)      AS shots_on_target,
                SUM(f.passes_total)         AS passes_total,
                SUM(f.passes_key)           AS key_passes,
                AVG(f.pass_accuracy)        AS avg_pass_accuracy,
                SUM(f.tackles_total)        AS tackles,
                SUM(f.tackles_interceptions) AS interceptions,
                SUM(f.duels_total)          AS duels_total,
                SUM(f.duels_won)            AS duels_won,
                CASE WHEN SUM(f.duels_total) > 0
                     THEN ROUND(100.0 * SUM(f.duels_won) / SUM(f.duels_total), 1)
                END                         AS duel_win_pct,
                SUM(f.dribbles_success)     AS successful_dribbles,
                SUM(f.fouls_committed)      AS fouls_committed,
                SUM(f.fouls_drawn)          AS fouls_drawn,
                SUM(f.yellow_cards)         AS yellow_cards,
                SUM(f.red_cards)            AS red_cards,
                SUM(f.penalty_scored)       AS penalties_scored,
                SUM(f.saves)                AS saves,
                SUM(f.goals_conceded)       AS goals_conceded,
                AVG(f.rating)               AS avg_rating
            FROM dim_player p
            LEFT JOIN dim_team t_wc
                ON p.world_cup_team_id = t_wc.team_id
            LEFT JOIN fact_player_season_stats f
                ON p.player_id = f.player_id
            LEFT JOIN dim_competition c
                ON f.competition_id = c.competition_id
               AND c.season = ?
            WHERE p.api_player_id IN ({placeholders})
            GROUP BY
                p.api_player_id, p.name, p.nationality,
                p.position, p.age, t_wc.name
            ORDER BY AVG(f.rating) DESC NULLS LAST
        """

        params = [season] + list(player_ids)
        df = self.db.query(sql, params)
        logger.info(
            "compare_players(%s, season=%d): %d players found",
            player_ids,
            season,
            len(df),
        )
        return df
