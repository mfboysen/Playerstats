#!/usr/bin/env python3
"""
World Cup 2026 Player Statistics Pipeline
==========================================

CLI entry point for running the ETL pipeline.

Usage:
    python pipeline.py init-db
    python pipeline.py fetch-teams [--season 2026]
    python pipeline.py fetch-squads [--season 2026]
    python pipeline.py fetch-player-stats [--season 2025] [--world-cup-only]
    python pipeline.py fetch-league-stats [--leagues 39,140,78,135,61] [--season 2025]
    python pipeline.py run-all [--season 2025]
"""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

# ── Load .env before importing project modules ──────────────────────────────
load_dotenv()

from src.api.client import FootballAPIClient
from src.db.database import Database
from src.etl.extract import Extractor
from src.etl.load import Loader
from src.etl.transform import (
    enrich_players_from_stats,
    generate_date_dimension,
    transform_competitions,
    transform_player_stats,
    transform_players,
    transform_teams,
)

# ── Logging setup ────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
)
logger = logging.getLogger("pipeline")
console = Console()

# ── Default league IDs ───────────────────────────────────────────────────────
DEFAULT_LEAGUES = [39, 140, 78, 135, 61, 2, 88, 94, 203]  # see README for names

# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_env(key: str) -> str:
    """Read a required environment variable, exit with an error if missing."""
    val = os.getenv(key)
    if not val:
        console.print(
            f"[red]Error:[/red] environment variable [bold]{key}[/bold] is not set.\n"
            "Copy [bold].env.example[/bold] to [bold].env[/bold] and fill in your API key.",
        )
        sys.exit(1)
    return val


def _make_db() -> Database:
    db_path = os.getenv("DB_PATH", "playerstats.db")
    return Database(db_path)


def _make_client() -> FootballAPIClient:
    api_key = _get_env("API_FOOTBALL_KEY")
    return FootballAPIClient(api_key=api_key)


def _estimate_calls(n_players: int, n_seasons: int, n_leagues: int, n_pages_per_league: int = 20) -> int:
    """Rough estimate of total API calls for a run-all."""
    team_calls = 1
    squad_calls = 32          # ~32 WC teams
    player_stat_calls = n_players * n_seasons
    league_calls = n_leagues * n_pages_per_league
    return team_calls + squad_calls + player_stat_calls + league_calls


def _print_summary_table(title: str, data: dict) -> None:
    table = Table(title=title, show_header=False, expand=False)
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="white")
    for k, v in data.items():
        table.add_row(str(k), str(v))
    console.print(table)


# ── Sub-commands ─────────────────────────────────────────────────────────────

def cmd_init_db(args: argparse.Namespace) -> None:
    """Create the database schema."""
    console.print(Panel("[bold]Initialising database schema[/bold]", style="blue"))
    with _make_db() as db:
        db.initialize_schema()
        # Populate date dimension
        dates = generate_date_dimension(start_year=2024, end_year=2027)
        loader = Loader(db)
        n = loader.load_date_dimension(dates)
        console.print(f"[green]Schema ready.[/green] Date dimension: [bold]{n}[/bold] rows loaded.")


def cmd_fetch_teams(args: argparse.Namespace) -> None:
    """Download World Cup teams and upsert into dim_team."""
    season = args.season
    console.print(
        Panel(f"[bold]Fetching World Cup {season} teams[/bold]", style="blue")
    )
    client = _make_client()
    extractor = Extractor(client)
    raw_teams = extractor.extract_world_cup_teams(season=season)

    team_rows = transform_teams(raw_teams)
    with _make_db() as db:
        loader = Loader(db)
        n = loader.upsert_teams(team_rows)

    _print_summary_table(
        "fetch-teams summary",
        {
            "Season": season,
            "Teams fetched": len(raw_teams),
            "Rows upserted": n,
            "API calls": client._request_count,
        },
    )


def cmd_fetch_squads(args: argparse.Namespace) -> None:
    """Download squad rosters for all World Cup teams in the DB."""
    console.print(Panel("[bold]Fetching World Cup squads[/bold]", style="blue"))

    with _make_db() as db:
        loader = Loader(db)
        team_map = loader.get_team_id_map()

        if not team_map:
            console.print(
                "[yellow]No teams found in DB. Run [bold]fetch-teams[/bold] first.[/yellow]"
            )
            return

        # Build list of raw team objects from DB for the extractor
        teams_df = db.query(
            "SELECT api_team_id, name FROM dim_team WHERE is_world_cup_2026 = TRUE"
        )

    raw_teams_stub = [
        {"team": {"id": int(row["api_team_id"]), "name": row["name"]}}
        for _, row in teams_df.iterrows()
    ]

    client = _make_client()
    extractor = Extractor(client)
    raw_squads = extractor.extract_world_cup_squads(teams=raw_teams_stub)

    with _make_db() as db:
        loader = Loader(db)
        team_id_map = loader.get_team_id_map()
        player_rows = transform_players(raw_squads, wc_team_map=team_id_map)
        n = loader.upsert_players(player_rows)

    _print_summary_table(
        "fetch-squads summary",
        {
            "Teams processed": len(raw_squads),
            "Players upserted": n,
            "API calls": client._request_count,
        },
    )


def cmd_fetch_player_stats(args: argparse.Namespace) -> None:
    """Fetch club-season stats for all players in the DB."""
    season = args.season
    world_cup_only = args.world_cup_only
    console.print(
        Panel(
            f"[bold]Fetching player stats (season {season})[/bold]"
            + (" [WC players only]" if world_cup_only else ""),
            style="blue",
        )
    )

    with _make_db() as db:
        if world_cup_only:
            players_df = db.query(
                "SELECT api_player_id FROM dim_player WHERE world_cup_team_id IS NOT NULL"
            )
        else:
            players_df = db.query("SELECT api_player_id FROM dim_player")

    if players_df.empty:
        console.print(
            "[yellow]No players in DB. Run [bold]fetch-squads[/bold] first.[/yellow]"
        )
        return

    player_ids = [int(x) for x in players_df["api_player_id"].tolist()]
    logger.info("Will fetch stats for %d players", len(player_ids))

    # Warn about daily limits
    estimated_calls = len(player_ids)
    if estimated_calls > 80:
        console.print(
            f"[yellow]Warning:[/yellow] This will make ~[bold]{estimated_calls}[/bold] API calls. "
            "Free tier allows 100/day. Consider running in batches.",
        )

    client = _make_client()
    extractor = Extractor(client)
    raw_stats = extractor.extract_player_stats(
        player_ids=player_ids, seasons=[season]
    )

    with _make_db() as db:
        loader = Loader(db)

        # Upsert competitions
        comp_rows = transform_competitions(raw_stats)
        loader.upsert_competitions(comp_rows)

        # Enrich / create players with full bio from stats responses
        _df_ep = db.query("SELECT api_player_id, world_cup_team_id FROM dim_player")
        existing_players = {
            int(row["api_player_id"]): {
                "world_cup_team_id": int(row["world_cup_team_id"]) if row.get("world_cup_team_id") is not None else None
            }
            for _, row in _df_ep.iterrows()
        }
        enriched_players = enrich_players_from_stats(raw_stats, existing_players)
        loader.upsert_players(enriched_players)

        # Upsert fact rows
        player_id_map = loader.get_player_id_map()
        team_id_map = loader.get_team_id_map()
        comp_id_map = loader.get_competition_id_map()

        # Ensure club teams referenced in stats exist in dim_team
        _ensure_club_teams(db, loader, raw_stats, team_id_map)
        team_id_map = loader.get_team_id_map()  # refresh after inserts

        fact_rows = transform_player_stats(
            raw_stats,
            player_id_map=player_id_map,
            team_id_map=team_id_map,
            competition_id_map=comp_id_map,
        )
        n_facts = loader.upsert_player_stats(fact_rows)

    _print_summary_table(
        "fetch-player-stats summary",
        {
            "Season": season,
            "Players queried": len(player_ids),
            "Stat records fetched": len(raw_stats),
            "Fact rows upserted": n_facts,
            "API calls": client._request_count,
        },
    )


def cmd_fetch_league_stats(args: argparse.Namespace) -> None:
    """Fetch all player stats from specified top leagues."""
    season = args.season
    league_ids = [int(x.strip()) for x in args.leagues.split(",") if x.strip()]

    console.print(
        Panel(
            f"[bold]Fetching league stats (season {season})[/bold]\n"
            f"Leagues: {league_ids}",
            style="blue",
        )
    )

    # Rough call estimate
    estimated = len(league_ids) * 20  # ~20 pages per league
    if estimated > 80:
        console.print(
            f"[yellow]Warning:[/yellow] This could make ~[bold]{estimated}+[/bold] API calls "
            "(free tier: 100/day).",
        )

    client = _make_client()
    extractor = Extractor(client)
    raw_stats = extractor.extract_league_stats(
        league_ids=league_ids, season=season
    )

    with _make_db() as db:
        loader = Loader(db)

        # Competitions
        comp_rows = transform_competitions(raw_stats)
        loader.upsert_competitions(comp_rows)

        # Ensure club teams exist
        team_id_map = loader.get_team_id_map()
        _ensure_club_teams(db, loader, raw_stats, team_id_map)
        team_id_map = loader.get_team_id_map()

        # Enrich / create players
        _df_ep2 = db.query("SELECT api_player_id, world_cup_team_id FROM dim_player")
        existing_players = {
            int(row["api_player_id"]): {
                "world_cup_team_id": int(row["world_cup_team_id"]) if row.get("world_cup_team_id") is not None else None
            }
            for _, row in _df_ep2.iterrows()
        }
        enriched_players = enrich_players_from_stats(raw_stats, existing_players)
        loader.upsert_players(enriched_players)

        # Fact rows
        player_id_map = loader.get_player_id_map()
        team_id_map = loader.get_team_id_map()
        comp_id_map = loader.get_competition_id_map()

        fact_rows = transform_player_stats(
            raw_stats,
            player_id_map=player_id_map,
            team_id_map=team_id_map,
            competition_id_map=comp_id_map,
        )
        n_facts = loader.upsert_player_stats(fact_rows)

    _print_summary_table(
        "fetch-league-stats summary",
        {
            "Season": season,
            "Leagues": len(league_ids),
            "Player-stat records": len(raw_stats),
            "Fact rows upserted": n_facts,
            "API calls": client._request_count,
        },
    )


def cmd_run_all(args: argparse.Namespace) -> None:
    """Run the complete pipeline end-to-end."""
    season = args.season
    console.print(
        Panel(
            f"[bold cyan]Running full pipeline (club season {season})[/bold cyan]\n"
            "Steps: init-db → fetch-teams → fetch-squads → fetch-league-stats",
            style="cyan",
        )
    )

    # Estimate total calls
    league_ids = DEFAULT_LEAGUES
    estimated = 1 + 32 + len(league_ids) * 20
    console.print(
        f"[yellow]Estimated API calls:[/yellow] ~[bold]{estimated}[/bold] "
        "(free tier limit: 100/day — this may exceed it for all leagues).",
    )

    # Step 1 — init DB
    class FakeArgs:
        pass

    init_args = FakeArgs()
    cmd_init_db(init_args)

    # Step 2 — teams
    team_args = FakeArgs()
    team_args.season = 2026
    cmd_fetch_teams(team_args)

    # Step 3 — squads
    squad_args = FakeArgs()
    cmd_fetch_squads(squad_args)

    # Step 4 — league stats
    league_args = FakeArgs()
    league_args.season = season
    league_args.leagues = ",".join(str(lid) for lid in league_ids)
    cmd_fetch_league_stats(league_args)

    console.print(
        Panel("[bold green]Pipeline complete![/bold green]", style="green")
    )


# ── Club-team helper ─────────────────────────────────────────────────────────

def _ensure_club_teams(
    db: Database, loader: Loader, raw_stats: list, existing_team_map: dict
) -> None:
    """
    Insert club teams that appear in player stats but aren't in dim_team yet.
    These are not World Cup teams, so is_world_cup_2026 = False.
    """
    new_teams = []
    seen_api_ids = set(existing_team_map.keys())

    for record in raw_stats:
        for stat in record.get("statistics", []):
            team = stat.get("team", {})
            api_team_id = team.get("id")
            if api_team_id is None or api_team_id in seen_api_ids:
                continue
            seen_api_ids.add(api_team_id)
            new_teams.append(
                {
                    "api_team_id": api_team_id,
                    "name": team.get("name", "Unknown"),
                    "short_name": None,
                    "country": None,
                    "logo_url": team.get("logo"),
                    "is_world_cup_2026": False,
                }
            )

    if new_teams:
        logger.info("Inserting %d new club teams into dim_team", len(new_teams))
        loader.upsert_teams(new_teams)


# ── Argument parser ──────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="World Cup 2026 Player Statistics Pipeline",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init-db
    sub.add_parser("init-db", help="Initialise the DuckDB schema")

    # fetch-teams
    p_teams = sub.add_parser("fetch-teams", help="Fetch World Cup teams")
    p_teams.add_argument("--season", type=int, default=2026)

    # fetch-squads
    sub.add_parser("fetch-squads", help="Fetch squad rosters for all WC teams in DB")

    # fetch-player-stats
    p_ps = sub.add_parser(
        "fetch-player-stats", help="Fetch club-season stats for players in DB"
    )
    p_ps.add_argument("--season", type=int, default=2025)
    p_ps.add_argument(
        "--world-cup-only",
        action="store_true",
        default=False,
        help="Only fetch stats for players with a WC team assignment",
    )

    # fetch-league-stats
    p_ls = sub.add_parser(
        "fetch-league-stats", help="Fetch all player stats from top leagues"
    )
    p_ls.add_argument(
        "--leagues",
        default=",".join(str(lid) for lid in DEFAULT_LEAGUES),
        help="Comma-separated league IDs",
    )
    p_ls.add_argument("--season", type=int, default=2025)

    # run-all
    p_all = sub.add_parser("run-all", help="Run the complete pipeline")
    p_all.add_argument("--season", type=int, default=2025)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "init-db": cmd_init_db,
        "fetch-teams": cmd_fetch_teams,
        "fetch-squads": cmd_fetch_squads,
        "fetch-player-stats": cmd_fetch_player_stats,
        "fetch-league-stats": cmd_fetch_league_stats,
        "run-all": cmd_run_all,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    try:
        handler(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        sys.exit(0)
    except Exception as exc:
        logger.exception("Pipeline failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
