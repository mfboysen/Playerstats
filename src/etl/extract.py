"""
ETL Extract layer — pulls raw data from the API-Football client.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.api.client import FootballAPIClient

logger = logging.getLogger(__name__)


class Extractor:
    """Orchestrates data extraction from the API-Football service."""

    def __init__(self, client: "FootballAPIClient"):
        """
        Args:
            client: Authenticated FootballAPIClient instance
        """
        self.client = client

    # ------------------------------------------------------------------
    # World Cup teams
    # ------------------------------------------------------------------

    def extract_world_cup_teams(self, season: int = 2026) -> list[dict]:
        """
        Fetch raw World Cup team data.

        Args:
            season: World Cup year (default 2026)

        Returns:
            List of raw team objects as returned by the API
        """
        logger.info("Extracting World Cup %d teams...", season)
        raw = self.client.get_world_cup_teams(season=season)
        logger.info("Extracted %d World Cup teams", len(raw))
        return raw

    # ------------------------------------------------------------------
    # Squads
    # ------------------------------------------------------------------

    def extract_world_cup_squads(self, teams: list[dict]) -> list[dict]:
        """
        Fetch squads for each World Cup team.

        Args:
            teams: Raw team objects (as returned by extract_world_cup_teams)

        Returns:
            List of dicts:
                {
                    "team_id": <api_team_id>,
                    "team_name": <str>,
                    "players": [<player stub dicts>]
                }
        """
        results: list[dict] = []

        for entry in teams:
            team_info = entry.get("team", {})
            api_team_id = team_info.get("id")
            team_name = team_info.get("name", "Unknown")

            if api_team_id is None:
                logger.warning("Skipping team entry with no id: %s", entry)
                continue

            logger.info("Fetching squad for team '%s' (id=%s)", team_name, api_team_id)
            raw_squad = self.client.get_team_squad(team_id=api_team_id)

            # The API returns a list of {"team": {...}, "players": [...]}
            players: list[dict] = []
            for squad_entry in raw_squad:
                players.extend(squad_entry.get("players", []))

            results.append(
                {
                    "team_id": api_team_id,
                    "team_name": team_name,
                    "players": players,
                }
            )
            logger.info(
                "Team '%s': %d players in squad", team_name, len(players)
            )

        logger.info(
            "Extracted squads for %d teams (%d total players)",
            len(results),
            sum(len(r["players"]) for r in results),
        )
        return results

    # ------------------------------------------------------------------
    # Player stats
    # ------------------------------------------------------------------

    def extract_player_stats(
        self, player_ids: list[int], seasons: list[int]
    ) -> list[dict]:
        """
        Fetch season stats for a list of players.

        For each (player_id, season) combination, one API call is made.
        The flat list of raw API player-statistics responses is returned.

        Args:
            player_ids: List of API player IDs
            seasons: List of season years to fetch for each player

        Returns:
            Flat list of raw player+statistics dicts from the API
        """
        results: list[dict] = []
        total_combos = len(player_ids) * len(seasons)
        logger.info(
            "Extracting stats for %d players x %d seasons = %d calls",
            len(player_ids),
            len(seasons),
            total_combos,
        )

        for i, player_id in enumerate(player_ids, start=1):
            for season in seasons:
                logger.debug(
                    "[%d/%d] Fetching player_id=%d season=%d",
                    i,
                    len(player_ids),
                    player_id,
                    season,
                )
                try:
                    raw = self.client.get_player_stats(
                        player_id=player_id, season=season
                    )
                    results.extend(raw)
                except Exception as exc:
                    logger.warning(
                        "Failed to fetch player_id=%d season=%d: %s",
                        player_id,
                        season,
                        exc,
                    )

        logger.info("Extracted %d player-stat records", len(results))
        return results

    # ------------------------------------------------------------------
    # Match-level stats  (fixture list → per-fixture player stats)
    # ------------------------------------------------------------------

    def extract_match_stats(
        self, league_ids: list[int], season: int, status_filter: str = "FT"
    ) -> list[dict]:
        """
        Fetch per-match player statistics for multiple leagues.

        For each league:
          1. Downloads the full fixture list.
          2. Filters to completed matches (status_filter, default "FT").
          3. Fetches player stats for each fixture.

        Returns a flat list of enriched fixture objects:
            {
                "fixture_id": int,
                "fixture_date": str,          # ISO-8601
                "competition": {"id", "name", "season"},
                "home_team": {"id", "name", "logo"},
                "away_team": {"id", "name", "logo"},
                "home_goals": int | None,
                "away_goals": int | None,
                "venue": str | None,
                "city": str | None,
                "round": str | None,
                "status": str,
                "team_players": [             # raw /fixtures/players response
                    {"team": {...}, "players": [...]},
                    ...
                ]
            }

        Args:
            league_ids: API league IDs to process
            season: Season year (e.g. 2025)
            status_filter: Fixture status short code to include (default "FT")
        """
        results: list[dict] = []

        for league_id in league_ids:
            logger.info(
                "Extracting match stats for league=%d season=%d", league_id, season
            )
            try:
                raw_fixtures = self.client.get_all_fixtures(league_id, season)
            except Exception as exc:
                logger.warning(
                    "Failed to fetch fixtures for league=%d season=%d: %s",
                    league_id, season, exc,
                )
                continue

            completed = [
                f for f in raw_fixtures
                if f.get("fixture", {}).get("status", {}).get("short") == status_filter
            ]
            logger.info(
                "League %d: %d/%d fixtures completed (%s)",
                league_id, len(completed), len(raw_fixtures), status_filter,
            )

            for raw_fix in completed:
                fix_meta = raw_fix.get("fixture", {})
                fixture_id = fix_meta.get("id")
                if fixture_id is None:
                    continue

                league_meta = raw_fix.get("league", {})
                teams_meta = raw_fix.get("teams", {})
                goals_meta = raw_fix.get("goals", {})
                venue_meta = fix_meta.get("venue", {})

                try:
                    team_players = self.client.get_fixture_players(fixture_id)
                except Exception as exc:
                    logger.warning(
                        "Failed to fetch players for fixture_id=%d: %s", fixture_id, exc
                    )
                    team_players = []

                results.append(
                    {
                        "fixture_id": fixture_id,
                        "fixture_date": fix_meta.get("date"),
                        "competition": {
                            "id": league_meta.get("id"),
                            "name": league_meta.get("name"),
                            "country": league_meta.get("country"),
                            "logo": league_meta.get("logo"),
                            "season": league_meta.get("season"),
                            "type": "International"
                            if league_meta.get("country") in ("World", None)
                            else "League",
                        },
                        "home_team": teams_meta.get("home", {}),
                        "away_team": teams_meta.get("away", {}),
                        "home_goals": goals_meta.get("home"),
                        "away_goals": goals_meta.get("away"),
                        "venue": venue_meta.get("name"),
                        "city": venue_meta.get("city"),
                        "round": league_meta.get("round"),
                        "status": fix_meta.get("status", {}).get("long"),
                        "team_players": team_players,
                    }
                )

            logger.info(
                "League %d: enriched %d fixture records so far", league_id, len(results)
            )

        logger.info("Total enriched fixtures extracted: %d", len(results))
        return results

    # ------------------------------------------------------------------
    # League stats (season-level, used for player bio enrichment only)
    # ------------------------------------------------------------------

    def extract_league_stats(
        self, league_ids: list[int], season: int
    ) -> list[dict]:
        """
        Fetch all player stats from multiple leagues for a given season.

        Deduplicates entries by (player_id, league_id) — the last entry
        wins if duplicates exist within the same league page set.

        Args:
            league_ids: List of API league IDs to fetch
            season: Season year (e.g. 2025)

        Returns:
            Flat, deduplicated list of raw player+statistics dicts
        """
        seen: dict[tuple[int, int], dict] = {}  # (player_id, league_id) -> record

        for league_id in league_ids:
            logger.info(
                "Extracting all players for league=%d season=%d", league_id, season
            )
            try:
                raw_players = self.client.get_all_league_players(
                    league_id=league_id, season=season
                )
            except Exception as exc:
                logger.warning(
                    "Failed to fetch league=%d season=%d: %s",
                    league_id,
                    season,
                    exc,
                )
                continue

            for record in raw_players:
                player_info = record.get("player", {})
                pid = player_info.get("id")
                if pid is None:
                    continue
                key = (pid, league_id)
                seen[key] = record

            logger.info(
                "League %d: %d raw records, %d unique (player, league) pairs so far",
                league_id,
                len(raw_players),
                len(seen),
            )

        result = list(seen.values())
        logger.info(
            "Total unique (player, league) records extracted: %d", len(result)
        )
        return result
