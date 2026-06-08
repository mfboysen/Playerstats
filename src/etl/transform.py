"""
ETL Transform layer — pure functions that convert raw API data into
rows ready for DuckDB insertion.
"""

import logging
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
_DAY_NAMES = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]


def _safe_int(val, default: int = 0) -> int:
    """Convert a possibly-None value to int, returning *default* on failure."""
    if val is None:
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _safe_float(val, default=None):
    """Convert a possibly-None value to float, returning *default* on failure."""
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Date dimension
# ---------------------------------------------------------------------------

def generate_date_dimension(
    start_year: int = 2024, end_year: int = 2027
) -> list[dict]:
    """
    Generate all calendar dates in [start_year, end_year] as dim_date rows.

    Args:
        start_year: First year (inclusive)
        end_year: Last year (inclusive)

    Returns:
        List of dicts matching the dim_date schema
    """
    rows: list[dict] = []
    start = date(start_year, 1, 1)
    end = date(end_year, 12, 31)
    current = start

    while current <= end:
        iso_dow = current.isoweekday()  # 1=Monday … 7=Sunday
        dow_0indexed = iso_dow - 1       # 0=Monday … 6=Sunday
        rows.append(
            {
                "date_key": int(current.strftime("%Y%m%d")),
                "full_date": current.isoformat(),
                "year": current.year,
                "month": current.month,
                "month_name": _MONTH_NAMES[current.month],
                "quarter": (current.month - 1) // 3 + 1,
                "week_of_year": int(current.strftime("%W")),
                "day_of_week": dow_0indexed,
                "day_name": _DAY_NAMES[dow_0indexed],
                "is_weekend": dow_0indexed >= 5,  # Saturday=5, Sunday=6
            }
        )
        current += timedelta(days=1)

    logger.debug(
        "Generated %d date rows (%d-%d)", len(rows), start_year, end_year
    )
    return rows


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

def transform_teams(raw_teams: list) -> list[dict]:
    """
    Transform raw API team objects into dim_team rows.

    The `team_id` (surrogate PK) is NOT assigned here — it is assigned
    during load (using MAX(team_id)+1 or ON CONFLICT logic).

    Args:
        raw_teams: Raw objects from /teams endpoint

    Returns:
        List of dicts with keys matching dim_team columns (minus team_id)
    """
    rows: list[dict] = []
    for entry in raw_teams:
        team = entry.get("team", {})
        api_id = team.get("id")
        if api_id is None:
            logger.warning("Skipping team with no API id: %s", entry)
            continue
        venue = entry.get("venue", {})
        country = team.get("country") or (venue.get("city") and None)
        rows.append(
            {
                "api_team_id": api_id,
                "name": team.get("name", "Unknown"),
                "short_name": team.get("code"),          # e.g. "FRA"
                "country": team.get("country"),
                "logo_url": team.get("logo"),
                "is_world_cup_2026": True,
            }
        )

    logger.debug("Transformed %d team rows", len(rows))
    return rows


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------

def transform_players(raw_squads: list, wc_team_map: dict) -> list[dict]:
    """
    Transform squad data into dim_player rows.

    Args:
        raw_squads: Output of Extractor.extract_world_cup_squads — list of
                    {"team_id": api_team_id, "team_name": str, "players": [...]}
        wc_team_map: Mapping of api_team_id -> team_id (DB surrogate key)

    Returns:
        List of dicts with keys matching dim_player columns (minus player_id)
    """
    seen_player_ids: set[int] = set()
    rows: list[dict] = []

    for squad_entry in raw_squads:
        api_team_id = squad_entry.get("team_id")
        db_team_id = wc_team_map.get(api_team_id)

        for player in squad_entry.get("players", []):
            api_player_id = player.get("id")
            if api_player_id is None:
                continue
            if api_player_id in seen_player_ids:
                continue
            seen_player_ids.add(api_player_id)

            rows.append(
                {
                    "api_player_id": api_player_id,
                    "name": player.get("name", "Unknown"),
                    "firstname": player.get("firstname"),
                    "lastname": player.get("lastname"),
                    "nationality": None,         # squads endpoint doesn't include nationality
                    "birth_date": None,          # squads endpoint doesn't include birth info
                    "age": player.get("age"),
                    "height": None,
                    "weight": None,
                    "position": player.get("position"),
                    "photo_url": player.get("photo"),
                    "world_cup_team_id": db_team_id,
                }
            )

    logger.debug("Transformed %d player rows from squads", len(rows))
    return rows


# ---------------------------------------------------------------------------
# Competitions
# ---------------------------------------------------------------------------

def transform_competitions(raw_player_stats: list) -> list[dict]:
    """
    Extract unique competition records from player-stats API responses.

    Args:
        raw_player_stats: Flat list of raw player+statistics dicts

    Returns:
        List of dicts with keys matching dim_competition columns (minus competition_id)
    """
    seen: dict[tuple[int, int], dict] = {}  # (api_league_id, season) -> row

    for record in raw_player_stats:
        for stat in record.get("statistics", []):
            league = stat.get("league", {})
            api_league_id = league.get("id")
            season = league.get("season")
            if api_league_id is None or season is None:
                continue
            key = (api_league_id, season)
            if key in seen:
                continue

            # Infer type from league name/country heuristics
            name = league.get("name", "")
            country = league.get("country")
            if country in ("World", None) or "world cup" in name.lower() or "champions" in name.lower():
                comp_type = "International"
            elif "cup" in name.lower() or "fa cup" in name.lower():
                comp_type = "Cup"
            else:
                comp_type = "League"

            seen[key] = {
                "api_league_id": api_league_id,
                "name": name,
                "type": comp_type,
                "country": country,
                "logo_url": league.get("logo"),
                "season": season,
            }

    rows = list(seen.values())
    logger.debug("Transformed %d unique competition rows", len(rows))
    return rows


# ---------------------------------------------------------------------------
# Match dimension
# ---------------------------------------------------------------------------

def _parse_date_key(date_str: str | None) -> int | None:
    """Parse an ISO-8601 datetime string to a YYYYMMDD integer date key."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return int(dt.strftime("%Y%m%d"))
    except (ValueError, TypeError):
        return None


def transform_matches(
    enriched_fixtures: list,
    team_id_map: dict,
    competition_id_map: dict,
) -> list[dict]:
    """
    Transform enriched fixture objects into dim_match rows.

    Args:
        enriched_fixtures: Output of Extractor.extract_match_stats
        team_id_map: {api_team_id -> team_id}
        competition_id_map: {(api_league_id, season) -> competition_id}

    Returns:
        List of dicts with keys matching dim_match columns (minus match_id)
    """
    rows: list[dict] = []
    seen: set[int] = set()

    for fix in enriched_fixtures:
        fixture_id = fix.get("fixture_id")
        if fixture_id is None or fixture_id in seen:
            continue
        seen.add(fixture_id)

        comp = fix.get("competition", {})
        api_league_id = comp.get("id")
        season = comp.get("season")
        competition_id = competition_id_map.get((api_league_id, season))

        home_api_id = fix.get("home_team", {}).get("id")
        away_api_id = fix.get("away_team", {}).get("id")

        rows.append(
            {
                "api_fixture_id": fixture_id,
                "date_key": _parse_date_key(fix.get("fixture_date")),
                "competition_id": competition_id,
                "home_team_id": team_id_map.get(home_api_id),
                "away_team_id": team_id_map.get(away_api_id),
                "home_goals": fix.get("home_goals"),
                "away_goals": fix.get("away_goals"),
                "venue": fix.get("venue"),
                "city": fix.get("city"),
                "round": fix.get("round"),
                "status": fix.get("status"),
            }
        )

    logger.debug("Transformed %d match rows", len(rows))
    return rows


def transform_competitions_from_fixtures(enriched_fixtures: list) -> list[dict]:
    """
    Extract unique competition records from enriched fixture objects.

    Args:
        enriched_fixtures: Output of Extractor.extract_match_stats

    Returns:
        List of dicts matching dim_competition columns (minus competition_id)
    """
    seen: dict[tuple, dict] = {}
    for fix in enriched_fixtures:
        comp = fix.get("competition", {})
        api_league_id = comp.get("id")
        season = comp.get("season")
        if api_league_id is None or season is None:
            continue
        key = (api_league_id, season)
        if key in seen:
            continue

        name = comp.get("name", "")
        country = comp.get("country")
        if country in ("World", None) or "world cup" in name.lower() or "champions" in name.lower():
            comp_type = "International"
        elif "cup" in name.lower():
            comp_type = "Cup"
        else:
            comp_type = "League"

        seen[key] = {
            "api_league_id": api_league_id,
            "name": name,
            "type": comp_type,
            "country": country,
            "logo_url": comp.get("logo"),
            "season": season,
        }

    rows = list(seen.values())
    logger.debug("Transformed %d competition rows from fixtures", len(rows))
    return rows


def transform_teams_from_fixtures(enriched_fixtures: list) -> list[dict]:
    """
    Extract club teams referenced in fixtures that may not yet be in dim_team.

    Args:
        enriched_fixtures: Output of Extractor.extract_match_stats

    Returns:
        List of dicts matching dim_team columns (minus team_id), is_world_cup_2026=False
    """
    seen: dict[int, dict] = {}
    for fix in enriched_fixtures:
        for side in ("home_team", "away_team"):
            t = fix.get(side, {})
            api_id = t.get("id")
            if api_id is None or api_id in seen:
                continue
            seen[api_id] = {
                "api_team_id": api_id,
                "name": t.get("name", "Unknown"),
                "short_name": t.get("code"),
                "country": t.get("country"),
                "logo_url": t.get("logo"),
                "is_world_cup_2026": False,
            }
    return list(seen.values())


# ---------------------------------------------------------------------------
# Match-level player stats (fact)
# ---------------------------------------------------------------------------

def transform_match_player_stats(
    enriched_fixtures: list,
    player_id_map: dict,
    match_id_map: dict,
    team_id_map: dict,
    competition_id_map: dict,
    national_team_map: dict,
) -> list[dict]:
    """
    Transform enriched fixture objects into fact_player_match_stats rows.

    One row is produced per (player, match). For each player in a fixture,
    club_team_id is the team they played for, opponent_team_id is the other
    team, and national_team_id is their WC 2026 squad assignment (may be None
    for players not in any WC squad).

    Args:
        enriched_fixtures: Output of Extractor.extract_match_stats
        player_id_map: {api_player_id -> player_id}
        match_id_map: {api_fixture_id -> match_id}
        team_id_map: {api_team_id -> team_id}
        competition_id_map: {(api_league_id, season) -> competition_id}
        national_team_map: {api_player_id -> national_team_id (DB surrogate)}

    Returns:
        List of dicts matching fact_player_match_stats columns (minus stat_id)
    """
    rows: list[dict] = []
    skipped_match = 0
    skipped_player = 0
    skipped_team = 0
    now = datetime.now(timezone.utc).isoformat()

    for fix in enriched_fixtures:
        fixture_id = fix.get("fixture_id")
        match_id = match_id_map.get(fixture_id)
        if match_id is None:
            skipped_match += 1
            continue

        date_key = _parse_date_key(fix.get("fixture_date"))
        comp = fix.get("competition", {})
        competition_id = competition_id_map.get((comp.get("id"), comp.get("season")))

        home_api_id = fix.get("home_team", {}).get("id")
        away_api_id = fix.get("away_team", {}).get("id")
        home_team_id = team_id_map.get(home_api_id)
        away_team_id = team_id_map.get(away_api_id)

        for team_entry in fix.get("team_players", []):
            api_team_id = team_entry.get("team", {}).get("id")
            club_team_id = team_id_map.get(api_team_id)
            if club_team_id is None:
                skipped_team += 1
                continue

            # The opponent is whichever of home/away is not this team
            if api_team_id == home_api_id:
                opponent_team_id = away_team_id
            else:
                opponent_team_id = home_team_id

            if opponent_team_id is None:
                skipped_team += 1
                continue

            for player_entry in team_entry.get("players", []):
                player_info = player_entry.get("player", {})
                api_player_id = player_info.get("id")
                player_id = player_id_map.get(api_player_id)
                if player_id is None:
                    skipped_player += 1
                    continue

                stat_list = player_entry.get("statistics", [])
                stat = stat_list[0] if stat_list else {}

                games = stat.get("games", {})
                goals_data = stat.get("goals", {})
                shots = stat.get("shots", {})
                passes = stat.get("passes", {})
                tackles = stat.get("tackles", {})
                duels = stat.get("duels", {})
                dribbles = stat.get("dribbles", {})
                fouls = stat.get("fouls", {})
                cards = stat.get("cards", {})
                penalty = stat.get("penalty", {})

                rows.append(
                    {
                        "player_id": player_id,
                        "match_id": match_id,
                        "date_key": date_key,
                        "club_team_id": club_team_id,
                        "opponent_team_id": opponent_team_id,
                        "competition_id": competition_id,
                        "national_team_id": national_team_map.get(api_player_id),
                        # Playing time
                        "minutes_played": _safe_int(games.get("minutes")),
                        "is_starter": not bool(games.get("substitute", False)),
                        # Attack
                        "goals": _safe_int(goals_data.get("total")),
                        "assists": _safe_int(goals_data.get("assists")),
                        "shots_total": _safe_int(shots.get("total")),
                        "shots_on_target": _safe_int(shots.get("on")),
                        "offsides": _safe_int(stat.get("offsides")),
                        # Passing
                        "passes_total": _safe_int(passes.get("total")),
                        "passes_key": _safe_int(passes.get("key")),
                        "pass_accuracy": _safe_float(passes.get("accuracy")),
                        # Defense
                        "tackles_total": _safe_int(tackles.get("total")),
                        "tackles_blocks": _safe_int(tackles.get("blocks")),
                        "tackles_interceptions": _safe_int(tackles.get("interceptions")),
                        # Duels
                        "duels_total": _safe_int(duels.get("total")),
                        "duels_won": _safe_int(duels.get("won")),
                        # Dribbles
                        "dribbles_attempts": _safe_int(dribbles.get("attempts")),
                        "dribbles_success": _safe_int(dribbles.get("success")),
                        "dribbles_past": _safe_int(dribbles.get("past")),
                        # Discipline
                        "fouls_drawn": _safe_int(fouls.get("drawn")),
                        "fouls_committed": _safe_int(fouls.get("committed")),
                        "yellow_cards": _safe_int(cards.get("yellow")),
                        "red_cards": _safe_int(cards.get("red")),
                        # Penalties
                        "penalty_won": _safe_int(penalty.get("won")),
                        "penalty_committed": _safe_int(penalty.get("commited")),  # typo in API
                        "penalty_scored": _safe_int(penalty.get("scored")),
                        "penalty_missed": _safe_int(penalty.get("missed")),
                        "penalty_saved": _safe_int(penalty.get("saved")),
                        # Goalkeeper
                        "saves": _safe_int(goals_data.get("saves")),
                        "goals_conceded": _safe_int(goals_data.get("conceded")),
                        # Rating
                        "rating": _safe_float(games.get("rating")),
                        "updated_at": now,
                    }
                )

    logger.info(
        "Transformed %d match-stat rows (skipped: %d no-match, %d no-player, %d no-team)",
        len(rows), skipped_match, skipped_player, skipped_team,
    )
    return rows


# ---------------------------------------------------------------------------
# Player enrichment from full player-stats responses
# ---------------------------------------------------------------------------

def enrich_players_from_stats(
    raw_player_stats: list,
    existing_players: dict,
) -> list[dict]:
    """
    Extract / update dim_player rows from full player-stats API responses.

    Players returned by /players?id=... or /players?league=... include
    full bio (nationality, birth date, height, weight) that squad responses
    lack.  This function produces updated rows for existing players and
    new rows for players not yet in the DB.

    Args:
        raw_player_stats: Flat list of raw API player+statistics dicts
        existing_players: {api_player_id -> {"world_cup_team_id": ...}}
                          — used to preserve world_cup_team_id

    Returns:
        List of dicts with keys matching dim_player columns (minus player_id)
    """
    seen: dict[int, dict] = {}

    for record in raw_player_stats:
        player_info = record.get("player", {})
        api_player_id = player_info.get("id")
        if api_player_id is None or api_player_id in seen:
            continue

        birth = player_info.get("birth", {})
        birth_date_str = birth.get("date")

        # Derive position from first statistics entry if available
        position = None
        for stat in record.get("statistics", []):
            position = stat.get("games", {}).get("position")
            if position:
                break

        existing = existing_players.get(api_player_id, {})

        # Pick the club team from the stat with the most appearances
        club_api_team_id = None
        best_appearances = -1
        for stat in record.get("statistics", []):
            apps = _safe_int(stat.get("games", {}).get("appearences"))
            if apps > best_appearances:
                best_appearances = apps
                club_api_team_id = stat.get("team", {}).get("id")

        seen[api_player_id] = {
            "api_player_id": api_player_id,
            "name": player_info.get("name", "Unknown"),
            "firstname": player_info.get("firstname"),
            "lastname": player_info.get("lastname"),
            "nationality": player_info.get("nationality"),
            "birth_date": birth_date_str,
            "age": player_info.get("age"),
            "height": player_info.get("height"),
            "weight": player_info.get("weight"),
            "position": position,
            "photo_url": player_info.get("photo"),
            "world_cup_team_id": existing.get("world_cup_team_id"),
            "club_api_team_id": club_api_team_id,  # resolved to DB ID during load
        }

    rows = list(seen.values())
    logger.debug("Enriched/created %d player rows from stats responses", len(rows))
    return rows
