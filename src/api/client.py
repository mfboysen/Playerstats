"""
API-Football client for fetching World Cup 2026 player statistics.
Base URL: https://v3.football.api-sports.io
"""

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://v3.football.api-sports.io"


class FootballAPIClient:
    """Client for the API-Football (api-sports.io) service."""

    def __init__(self, api_key: str, requests_per_minute: int = 10):
        """
        Initialize the client.

        Args:
            api_key: API-Football API key
            requests_per_minute: Rate limit (default 10 for free tier)
        """
        self.api_key = api_key
        self.requests_per_minute = requests_per_minute
        # Minimum seconds between requests
        self._min_interval = 60.0 / requests_per_minute
        self._last_request_time: float = 0.0
        self._request_count: int = 0

        self.session = requests.Session()
        self.session.headers.update({
            "X-apisports-key": self.api_key,
            "Accept": "application/json",
        })

    def _throttle(self) -> None:
        """Enforce rate limiting between requests."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        wait = self._min_interval - elapsed
        if wait > 0:
            logger.debug("Rate limiting: sleeping %.2fs", wait)
            time.sleep(wait)
        self._last_request_time = time.monotonic()

    def _get(self, endpoint: str, params: dict | None = None) -> list:
        """
        Make a GET request to the API.

        Args:
            endpoint: API endpoint path (e.g. '/teams')
            params: Query parameters

        Returns:
            The 'response' list from the JSON payload

        Raises:
            requests.HTTPError: On HTTP errors
            ValueError: On API-level errors in the response body
        """
        self._throttle()

        url = f"{BASE_URL}{endpoint}"
        logger.debug("GET %s params=%s", url, params)

        response = self.session.get(url, params=params, timeout=30)
        self._request_count += 1
        logger.info(
            "API call #%d: %s %s -> HTTP %d",
            self._request_count,
            "GET",
            endpoint,
            response.status_code,
        )

        response.raise_for_status()

        data = response.json()

        # Check for API-level errors
        errors = data.get("errors", {})
        if errors:
            raise ValueError(f"API error on {endpoint}: {errors}")

        result = data.get("response", [])
        paging = data.get("paging", {})
        if paging:
            logger.debug(
                "Paging: page %d of %d",
                paging.get("current", 1),
                paging.get("total", 1),
            )

        return result

    def _get_with_paging(self, endpoint: str, params: dict | None = None) -> dict:
        """
        Make a GET request and return both the response and paging info.

        Returns:
            dict with 'response' and 'paging' keys
        """
        self._throttle()

        url = f"{BASE_URL}{endpoint}"
        logger.debug("GET %s params=%s", url, params)

        response = self.session.get(url, params=params, timeout=30)
        self._request_count += 1
        logger.info(
            "API call #%d: %s %s -> HTTP %d",
            self._request_count,
            "GET",
            endpoint,
            response.status_code,
        )

        response.raise_for_status()

        data = response.json()

        errors = data.get("errors", {})
        if errors:
            raise ValueError(f"API error on {endpoint}: {errors}")

        return {
            "response": data.get("response", []),
            "paging": data.get("paging", {"current": 1, "total": 1}),
        }

    def get_world_cup_teams(self, season: int = 2026) -> list:
        """
        Fetch all teams participating in the FIFA World Cup.

        Args:
            season: World Cup year (default 2026)

        Returns:
            List of raw team objects from the API
        """
        logger.info("Fetching World Cup %d teams (league=1)...", season)
        result = self._get("/teams", params={"league": 1, "season": season})
        logger.info("Found %d World Cup teams", len(result))
        return result

    def get_team_squad(self, team_id: int) -> list:
        """
        Fetch the squad for a given team.

        Args:
            team_id: API team ID

        Returns:
            List of squad objects: [{"team": {...}, "players": [...]}]
        """
        logger.info("Fetching squad for team_id=%d", team_id)
        result = self._get("/players/squads", params={"team": team_id})
        logger.info(
            "Squad for team %d: %d entry/entries",
            team_id,
            len(result),
        )
        return result

    def get_player_stats(self, player_id: int, season: int) -> list:
        """
        Fetch stats for a single player for a given season.

        Args:
            player_id: API player ID
            season: Season year (e.g. 2025)

        Returns:
            List of player+statistics objects
        """
        logger.debug("Fetching stats for player_id=%d season=%d", player_id, season)
        result = self._get(
            "/players", params={"id": player_id, "season": season}
        )
        return result

    def get_league_players_page(
        self, league_id: int, season: int, page: int = 1
    ) -> dict:
        """
        Fetch one page of players from a league.

        Args:
            league_id: API league ID
            season: Season year
            page: Page number (1-based)

        Returns:
            dict with 'players' (list) and 'total_pages' (int)
        """
        logger.debug(
            "Fetching league %d season %d page %d", league_id, season, page
        )
        raw = self._get_with_paging(
            "/players",
            params={"league": league_id, "season": season, "page": page},
        )
        paging = raw["paging"]
        total_pages = paging.get("total", 1)
        return {
            "players": raw["response"],
            "total_pages": total_pages,
        }

    def get_all_league_players(self, league_id: int, season: int) -> list:
        """
        Fetch all players from a league across all pages.

        Args:
            league_id: API league ID
            season: Season year

        Returns:
            Flat list of player+statistics objects
        """
        logger.info(
            "Fetching all players for league %d season %d (page 1)...",
            league_id,
            season,
        )
        first_page = self.get_league_players_page(league_id, season, page=1)
        total_pages = first_page["total_pages"]
        all_players: list[Any] = list(first_page["players"])

        logger.info(
            "League %d: %d total pages, fetching remaining...",
            league_id,
            total_pages,
        )
        for page in range(2, total_pages + 1):
            page_data = self.get_league_players_page(league_id, season, page=page)
            all_players.extend(page_data["players"])
            logger.info(
                "League %d: fetched page %d/%d (total so far: %d)",
                league_id,
                page,
                total_pages,
                len(all_players),
            )

        logger.info(
            "League %d season %d: %d total players fetched",
            league_id,
            season,
            len(all_players),
        )
        return all_players
