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
    transform_competitions_from_fixtures,
    transform_match_player_stats,
    transform_matches,
    transform_players,
    transform_teams,
    transform_teams_from_fixtures,
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


def cmd_enrich_players(args: argparse.Namespace) -> None:
    """
    Enrich WC player records with club team info and full bio.

    Calls /players?id={id}&season={season} for every World Cup player in
    the DB. This populates nationality, height, weight, and crucially the
    club team they currently play for — which is needed by fetch-wc-match-stats.

    API calls: 1 per WC player (~736 total for a full 32-team tournament).
    """
    season = args.season
    console.print(
        Panel(
            f"[bold]Enriching WC player data (season {season})[/bold]\n"
            "Fetches club team, nationality, height, weight for each WC player.\n"
            "[dim]~1 API call per player[/dim]",
            style="blue",
        )
    )

    with _make_db() as db:
        players_df = db.query(
            "SELECT api_player_id FROM dim_player WHERE world_cup_team_id IS NOT NULL"
        )

    if players_df.empty:
        console.print("[yellow]No WC players in DB. Run fetch-squads first.[/yellow]")
        return

    player_ids = [int(x) for x in players_df["api_player_id"].tolist()]
    logger.info("Enriching %d WC players", len(player_ids))

    client = _make_client()
    extractor = Extractor(client)
    raw_stats = extractor.extract_player_stats(player_ids=player_ids, seasons=[season])

    with _make_db() as db:
        loader = Loader(db)

        # Insert club teams that appear in the stats but aren't in dim_team yet
        comp_rows = transform_competitions(raw_stats)
        loader.upsert_competitions(comp_rows)

        existing_team_map = loader.get_team_id_map()
        new_club_teams = []
        seen_ids = set(existing_team_map.keys())
        for record in raw_stats:
            for stat in record.get("statistics", []):
                t = stat.get("team", {})
                api_id = t.get("id")
                if api_id and api_id not in seen_ids:
                    seen_ids.add(api_id)
                    new_club_teams.append({
                        "api_team_id": api_id,
                        "name": t.get("name", "Unknown"),
                        "short_name": None,
                        "country": None,
                        "logo_url": t.get("logo"),
                        "is_world_cup_2026": False,
                    })
        if new_club_teams:
            loader.upsert_teams(new_club_teams)

        # Enrich players with full bio + club team ID
        existing_players_df = db.query("SELECT api_player_id, world_cup_team_id FROM dim_player")
        existing_players = {
            int(row["api_player_id"]): {
                "world_cup_team_id": int(row["world_cup_team_id"]) if row.get("world_cup_team_id") is not None else None
            }
            for _, row in existing_players_df.iterrows()
        }
        enriched = enrich_players_from_stats(raw_stats, existing_players)
        n_players = loader.upsert_players(enriched)

    _print_summary_table(
        "enrich-players summary",
        {
            "Season": season,
            "Players enriched": len(player_ids),
            "Raw stat records": len(raw_stats),
            "Players updated": n_players,
            "API calls": client._request_count,
        },
    )


def cmd_fetch_wc_match_stats(args: argparse.Namespace) -> None:
    """
    Fetch per-match stats for every club that has a World Cup player.

    Reads club teams from dim_player (set by enrich-players), fetches all
    completed fixtures for each club, then fetches player stats per fixture.
    Only World Cup players (already in dim_player) are written to the fact table.

    API calls: 1 per club (fixture list) + 1 per completed match.
    Fixtures shared between two WC players' clubs are fetched only once.
    """
    season = args.season
    console.print(
        Panel(
            f"[bold]Fetching WC player match stats (season {season})[/bold]\n"
            "Strategy: WC squads → their clubs → club fixtures → per-match player stats\n"
            "[dim]Only WC players are written to the fact table[/dim]",
            style="blue",
        )
    )

    # Look up the unique club teams that WC players play for
    with _make_db() as db:
        clubs_df = db.query("""
            SELECT DISTINCT ct.api_team_id, ct.name
            FROM dim_player p
            JOIN dim_team ct ON p.club_team_id = ct.team_id
            WHERE p.world_cup_team_id IS NOT NULL
              AND p.club_team_id IS NOT NULL
        """)

    if clubs_df.empty:
        console.print(
            "[yellow]No club teams found for WC players. Run enrich-players first.[/yellow]"
        )
        return

    wc_club_teams = [
        {"api_team_id": int(row["api_team_id"]), "name": row["name"]}
        for _, row in clubs_df.iterrows()
    ]
    logger.info(
        "Found %d unique club teams with WC players", len(wc_club_teams)
    )

    client = _make_client()
    extractor = Extractor(client)
    enriched_fixtures = extractor.extract_wc_player_match_stats(
        wc_club_teams=wc_club_teams, season=season
    )

    if not enriched_fixtures:
        console.print("[yellow]No completed fixtures found.[/yellow]")
        return

    with _make_db() as db:
        loader = Loader(db)

        comp_rows = transform_competitions_from_fixtures(enriched_fixtures)
        loader.upsert_competitions(comp_rows)

        team_rows = transform_teams_from_fixtures(enriched_fixtures)
        existing_map = loader.get_team_id_map()
        new_teams = [t for t in team_rows if t["api_team_id"] not in existing_map]
        if new_teams:
            loader.upsert_teams(new_teams)

        team_id_map = loader.get_team_id_map()
        comp_id_map = loader.get_competition_id_map()

        match_rows = transform_matches(enriched_fixtures, team_id_map, comp_id_map)
        n_matches = loader.upsert_matches(match_rows)

        match_id_map = loader.get_match_id_map()
        player_id_map = loader.get_player_id_map()
        national_team_map = loader.get_national_team_map()

        fact_rows = transform_match_player_stats(
            enriched_fixtures,
            player_id_map=player_id_map,
            match_id_map=match_id_map,
            team_id_map=team_id_map,
            competition_id_map=comp_id_map,
            national_team_map=national_team_map,
        )
        n_facts = loader.upsert_match_player_stats(fact_rows)

    _print_summary_table(
        "fetch-wc-match-stats summary",
        {
            "Season": season,
            "WC club teams": len(wc_club_teams),
            "Fixtures processed": len(enriched_fixtures),
            "Matches upserted": n_matches,
            "Fact rows (WC players only)": n_facts,
            "API calls": client._request_count,
        },
    )


def cmd_run_all(args: argparse.Namespace) -> None:
    """Run the complete pipeline end-to-end."""
    season = args.season
    console.print(
        Panel(
            f"[bold cyan]Running full pipeline (club season {season})[/bold cyan]\n"
            "Steps: init-db → fetch-teams → fetch-squads → enrich-players → fetch-wc-match-stats",
            style="cyan",
        )
    )

    class FakeArgs:
        pass

    cmd_init_db(FakeArgs())

    team_args = FakeArgs()
    team_args.season = 2026
    cmd_fetch_teams(team_args)

    cmd_fetch_squads(FakeArgs())

    enrich_args = FakeArgs()
    enrich_args.season = season
    cmd_enrich_players(enrich_args)

    match_args = FakeArgs()
    match_args.season = season
    cmd_fetch_wc_match_stats(match_args)

    console.print(Panel("[bold green]Pipeline complete![/bold green]", style="green"))


# ── Argument parser ──────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="World Cup 2026 Player Statistics Pipeline",
    )
    sub = parser.add_subparsers(dest="command")

    # init-db
    sub.add_parser("init-db", help="Initialise the DuckDB schema")

    # fetch-teams
    p_teams = sub.add_parser("fetch-teams", help="Fetch World Cup teams")
    p_teams.add_argument("--season", type=int, default=2026)

    # fetch-squads
    sub.add_parser("fetch-squads", help="Fetch squad rosters for all WC teams in DB")

    # enrich-players
    p_enrich = sub.add_parser(
        "enrich-players",
        help="Fetch club team and full bio for each WC player",
    )
    p_enrich.add_argument("--season", type=int, default=2025)

    # fetch-wc-match-stats
    p_ms = sub.add_parser(
        "fetch-wc-match-stats",
        help="Fetch per-match stats for clubs of WC players (player-first approach)",
    )
    p_ms.add_argument("--season", type=int, default=2025)

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
        "enrich-players": cmd_enrich_players,
        "fetch-wc-match-stats": cmd_fetch_wc_match_stats,
        "run-all": cmd_run_all,
    }

    if not args.command:
        console.print(
            Panel(
                "[bold]World Cup 2026 Player Statistics Pipeline[/bold]\n\n"
                "Run the steps below in order:\n\n"
                "  [cyan]python pipeline.py init-db[/cyan]\n"
                "  [cyan]python pipeline.py fetch-teams[/cyan]\n"
                "  [cyan]python pipeline.py fetch-squads[/cyan]\n"
                "  [cyan]python pipeline.py enrich-players --season 2025[/cyan]\n"
                "  [cyan]python pipeline.py fetch-wc-match-stats --season 2025[/cyan]\n\n"
                "Or run everything at once:\n\n"
                "  [cyan]python pipeline.py run-all --season 2025[/cyan]\n\n"
                "For full help:  [dim]python pipeline.py --help[/dim]",
                title="Usage",
                border_style="blue",
            )
        )
        sys.exit(0)

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
