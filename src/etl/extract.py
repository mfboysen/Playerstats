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
    # League stats
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
