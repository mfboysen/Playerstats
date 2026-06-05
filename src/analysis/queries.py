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
    "shots_total",
    "shots_on_target",
}


class Analytics:
    """High-level analytical queries over the World Cup player stats database."""

    def __init__(self, db: "Database"):
        self.db = db

    # ------------------------------------------------------------------
    # Player search / report
    # ------------------------------------------------------------------

    def search_player(self, name: str, season: int = 2025) -> pd.DataFrame:
        """
        Search for players by name (case-insensitive partial match).

        Returns a summary row per matching player — totals across all
        competitions in the given season.

        Returns:
            DataFrame: api_player_id, player_name, nationality, position,
            national_team, matches_played, goals, assists, avg_rating
        """
        sql = """
            SELECT
                p.api_player_id,
                p.name                          AS player_name,
                p.nationality,
                p.position,
                nt.name                         AS national_team,
                COUNT(DISTINCT f.match_id)      AS matches_played,
                SUM(f.goals)                    AS goals,
                SUM(f.assists)                  AS assists,
                SUM(f.minutes_played)           AS minutes,
                AVG(f.rating)                   AS avg_rating
            FROM dim_player p
            LEFT JOIN fact_player_match_stats f
                   ON p.player_id = f.player_id
            LEFT JOIN dim_competition c
                   ON f.competition_id = c.competition_id
                  AND c.season = ?
            LEFT JOIN dim_team nt
                   ON p.world_cup_team_id = nt.team_id
            WHERE LOWER(p.name) LIKE LOWER(?)
            GROUP BY
                p.api_player_id, p.name, p.nationality,
                p.position, nt.name
            ORDER BY SUM(f.goals) DESC NULLS LAST, p.name
        """
        df = self.db.query(sql, [season, f"%{name}%"])
        logger.info("search_player('%s', season=%d): %d rows", name, season, len(df))
        return df

    def player_report(self, api_player_id: int, season: int = 2025) -> dict:
        """
        Detailed match-by-match report for a single player.

        Returns:
            dict:
                'player'      : dict of dim_player bio
                'matches'     : DataFrame — one row per match, sorted by date
                'totals'      : dict of season totals / averages
        """
        player = self.db.query_one(
            """
            SELECT
                p.api_player_id,
                p.name, p.firstname, p.lastname,
                p.nationality, p.birth_date, p.age,
                p.height, p.weight, p.position, p.photo_url,
                nt.name AS national_team
            FROM dim_player p
            LEFT JOIN dim_team nt ON p.world_cup_team_id = nt.team_id
            WHERE p.api_player_id = ?
            """,
            [api_player_id],
        )

        if player is None:
            return {"player": None, "matches": pd.DataFrame(), "totals": {}}

        matches_df = self.db.query(
            """
            SELECT
                d.full_date                         AS date,
                ct.name                             AS club_team,
                ot.name                             AS opponent,
                c.name                              AS competition,
                f.minutes_played,
                f.is_starter,
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
                f.offsides,
                f.rating
            FROM fact_player_match_stats f
            JOIN dim_player      p  ON f.player_id        = p.player_id
            JOIN dim_team        ct ON f.club_team_id     = ct.team_id
            JOIN dim_team        ot ON f.opponent_team_id = ot.team_id
            JOIN dim_competition c  ON f.competition_id   = c.competition_id
            JOIN dim_date        d  ON f.date_key         = d.date_key
            WHERE p.api_player_id = ?
              AND c.season        = ?
            ORDER BY d.full_date
            """,
            [api_player_id, season],
        )

        totals: dict = {}
        if not matches_df.empty:
            sum_cols = [
                "minutes_played", "goals", "assists", "shots_total", "shots_on_target",
                "passes_total", "passes_key", "tackles_total", "tackles_interceptions",
                "tackles_blocks", "duels_total", "duels_won", "dribbles_attempts",
                "dribbles_success", "fouls_committed", "fouls_drawn",
                "yellow_cards", "red_cards", "penalty_scored", "saves", "goals_conceded",
                "offsides",
            ]
            totals["matches_played"] = len(matches_df)
            for col in sum_cols:
                if col in matches_df.columns:
                    totals[col] = int(matches_df[col].sum())
            if "rating" in matches_df.columns:
                totals["avg_rating"] = round(float(matches_df["rating"].mean()), 2)
            if "pass_accuracy" in matches_df.columns:
                totals["avg_pass_accuracy"] = round(
                    float(matches_df["pass_accuracy"].mean()), 2
                )

        return {"player": player, "matches": matches_df, "totals": totals}

    # ------------------------------------------------------------------
    # Team search / report
    # ------------------------------------------------------------------

    def search_team(self, name: str, season: int = 2025) -> pd.DataFrame:
        """Search World Cup teams by name (case-insensitive partial match)."""
        sql = """
            SELECT api_team_id, name, country, logo_url, is_world_cup_2026
            FROM dim_team
            WHERE is_world_cup_2026 = TRUE
              AND LOWER(name) LIKE LOWER(?)
            ORDER BY name
        """
        df = self.db.query(sql, [f"%{name}%"])
        logger.info("search_team('%s'): %d rows", name, len(df))
        return df

    def team_report(self, api_team_id: int, season: int = 2025) -> dict:
        """
        Detailed report for a World Cup national team.

        For each player in the squad, shows their season totals aggregated
        from all club matches in the given season.

        Returns:
            dict:
                'team'        : dict of dim_team info
                'squad_stats' : DataFrame — one row per player, sorted by rating desc
        """
        team = self.db.query_one(
            "SELECT * FROM dim_team WHERE api_team_id = ?", [api_team_id]
        )
        if team is None:
            return {"team": None, "squad_stats": pd.DataFrame()}

        squad_df = self.db.query(
            """
            SELECT
                p.api_player_id,
                p.name                              AS player_name,
                p.position,
                p.age,
                p.nationality,
                COUNT(DISTINCT f.match_id)          AS matches_played,
                SUM(f.minutes_played)               AS minutes,
                SUM(f.goals)                        AS goals,
                SUM(f.assists)                      AS assists,
                SUM(f.shots_total)                  AS shots_total,
                SUM(f.passes_total)                 AS passes_total,
                SUM(f.passes_key)                   AS key_passes,
                SUM(f.tackles_total)                AS tackles,
                SUM(f.tackles_interceptions)        AS interceptions,
                SUM(f.yellow_cards)                 AS yellow_cards,
                SUM(f.red_cards)                    AS red_cards,
                AVG(f.rating)                       AS avg_rating
            FROM dim_player p
            JOIN dim_team nt
                ON p.world_cup_team_id = nt.team_id
            LEFT JOIN fact_player_match_stats f
                ON p.player_id = f.player_id
            LEFT JOIN dim_competition c
                ON f.competition_id = c.competition_id
               AND c.season = ?
            WHERE nt.api_team_id = ?
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
        Rank players by a given statistic (aggregated across all matches in season).

        Args:
            stat: Column to rank by (must be in VALID_STAT_COLUMNS)
            season: Season year
            position: Optional position filter
            limit: Number of results
            world_cup_only: If True, only include WC 2026 squad players

        Raises:
            ValueError: if stat is not in VALID_STAT_COLUMNS
        """
        if stat not in VALID_STAT_COLUMNS:
            raise ValueError(
                f"Invalid stat '{stat}'. Valid: {sorted(VALID_STAT_COLUMNS)}"
            )

        agg_expr = f"AVG(f.{stat})" if stat == "rating" else f"SUM(f.{stat})"
        wc_filter = "AND f.national_team_id IS NOT NULL" if world_cup_only else ""
        pos_filter = "AND p.position = ?" if position else ""

        sql = f"""
            SELECT
                p.api_player_id,
                p.name              AS player_name,
                p.nationality,
                p.position,
                nt.name             AS national_team,
                {agg_expr}          AS {stat},
                COUNT(DISTINCT f.match_id)  AS matches_played,
                SUM(f.goals)        AS goals,
                SUM(f.assists)      AS assists,
                SUM(f.minutes_played) AS minutes,
                AVG(f.rating)       AS avg_rating
            FROM dim_player p
            LEFT JOIN dim_team nt
                ON p.world_cup_team_id = nt.team_id
            JOIN fact_player_match_stats f
                ON p.player_id = f.player_id
            JOIN dim_competition c
                ON f.competition_id = c.competition_id
            WHERE c.season = ?
              {wc_filter}
              {pos_filter}
            GROUP BY p.api_player_id, p.name, p.nationality, p.position, nt.name
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
            "top_players(stat=%s, season=%d, pos=%s, wc=%s): %d rows",
            stat, season, position, world_cup_only, len(df),
        )
        return df

    # ------------------------------------------------------------------
    # Compare players
    # ------------------------------------------------------------------

    def compare_players(
        self, player_ids: list[int], season: int = 2025
    ) -> pd.DataFrame:
        """Side-by-side match-aggregate comparison for a list of API player IDs."""
        if not player_ids:
            return pd.DataFrame()

        placeholders = ", ".join(["?"] * len(player_ids))

        sql = f"""
            SELECT
                p.api_player_id,
                p.name                              AS player_name,
                p.nationality,
                p.position,
                p.age,
                nt.name                             AS national_team,
                COUNT(DISTINCT f.match_id)          AS matches_played,
                SUM(f.minutes_played)               AS minutes,
                SUM(f.goals)                        AS goals,
                SUM(f.assists)                      AS assists,
                SUM(f.shots_total)                  AS shots_total,
                SUM(f.shots_on_target)              AS shots_on_target,
                SUM(f.passes_total)                 AS passes_total,
                SUM(f.passes_key)                   AS key_passes,
                AVG(f.pass_accuracy)                AS avg_pass_accuracy,
                SUM(f.tackles_total)                AS tackles,
                SUM(f.tackles_interceptions)        AS interceptions,
                SUM(f.duels_total)                  AS duels_total,
                SUM(f.duels_won)                    AS duels_won,
                CASE WHEN SUM(f.duels_total) > 0
                     THEN ROUND(100.0 * SUM(f.duels_won) / SUM(f.duels_total), 1)
                END                                 AS duel_win_pct,
                SUM(f.dribbles_success)             AS successful_dribbles,
                SUM(f.fouls_committed)              AS fouls_committed,
                SUM(f.fouls_drawn)                  AS fouls_drawn,
                SUM(f.yellow_cards)                 AS yellow_cards,
                SUM(f.red_cards)                    AS red_cards,
                SUM(f.penalty_scored)               AS penalties_scored,
                SUM(f.saves)                        AS saves,
                SUM(f.goals_conceded)               AS goals_conceded,
                AVG(f.rating)                       AS avg_rating
            FROM dim_player p
            LEFT JOIN dim_team nt
                ON p.world_cup_team_id = nt.team_id
            LEFT JOIN fact_player_match_stats f
                ON p.player_id = f.player_id
            LEFT JOIN dim_competition c
                ON f.competition_id = c.competition_id
               AND c.season = ?
            WHERE p.api_player_id IN ({placeholders})
            GROUP BY
                p.api_player_id, p.name, p.nationality,
                p.position, p.age, nt.name
            ORDER BY AVG(f.rating) DESC NULLS LAST
        """

        df = self.db.query(sql, [season] + list(player_ids))
        logger.info("compare_players(%s, season=%d): %d found", player_ids, season, len(df))
        return df
