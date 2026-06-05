#!/usr/bin/env python3
"""
World Cup 2026 Player Statistics — Terminal Reports
====================================================

CLI for querying and displaying player/team statistics using rich formatting.

Usage:
    python report.py player "Mbappe" [--season 2025]
    python report.py team "France" [--season 2025]
    python report.py top --stat goals [--season 2025] [--position Attacker] [--limit 20] [--all-players]
    python report.py compare --players "Mbappe,Haaland" [--season 2025]
"""

import argparse
import os
import sys

from dotenv import load_dotenv
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.prompt import IntPrompt
from rich.table import Table
from rich.text import Text

load_dotenv()

from src.analysis.queries import Analytics, VALID_STAT_COLUMNS
from src.db.database import Database

console = Console()

DATA_SOURCE_FOOTER = "Data: API-Football | Season: {season}"

# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt(val, decimals: int = 0, suffix: str = "") -> str:
    """Format a numeric value gracefully, returning '-' for None/NaN."""
    if val is None:
        return "-"
    try:
        import math
        if math.isnan(float(val)):
            return "-"
        if decimals == 0:
            return f"{int(val)}{suffix}"
        return f"{float(val):.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return str(val) if val else "-"


def _player_info_panel(player: dict) -> Panel:
    """Build a rich Panel with a player's biographical information."""
    lines = [
        f"[bold]{player.get('name', 'Unknown')}[/bold]",
        "",
        f"Nationality : {player.get('nationality') or '-'}",
        f"Position    : {player.get('position') or '-'}",
        f"Age         : {player.get('age') or '-'}",
        f"Birth       : {player.get('birth_date') or '-'}",
        f"Height      : {player.get('height') or '-'}",
        f"Weight      : {player.get('weight') or '-'}",
        f"World Cup   : {player.get('world_cup_team') or 'Not in WC 2026 squad'}",
    ]
    return Panel("\n".join(lines), title="[cyan]Player Info[/cyan]", expand=False)


def _make_stats_table(df, title: str = "Statistics") -> Table:
    """Build a rich Table from a DataFrame of competition stats."""
    table = Table(
        title=title,
        show_header=True,
        header_style="bold magenta",
        row_styles=["", "dim"],
    )

    col_map = [
        ("Club",          "club",             "cyan",    False),
        ("Competition",   "competition",       "white",   False),
        ("Apps",          "appearances",       "green",   False),
        ("Mins",          "minutes",           "green",   False),
        ("Goals",         "goals",             "yellow",  False),
        ("Assists",       "assists",           "yellow",  False),
        ("Shots",         "shots_total",       "white",   False),
        ("On Target",     "shots_on_target",   "white",   False),
        ("Key Passes",    "passes_key",        "white",   False),
        ("Pass Acc%",     "pass_accuracy",     "white",   True),
        ("Tackles",       "tackles_total",     "blue",    False),
        ("Interceptions", "tackles_interceptions", "blue", False),
        ("Yellow",        "yellow_cards",      "yellow",  False),
        ("Red",           "red_cards",         "red",     False),
        ("Rating",        "rating",            "cyan",    True),
    ]

    for col_name, _, style, is_float in col_map:
        table.add_column(col_name, style=style, justify="right" if col_name not in ("Club", "Competition") else "left")

    for _, row in df.iterrows():
        cells = []
        for col_name, key, style, is_float in col_map:
            val = row.get(key)
            if is_float:
                cells.append(_fmt(val, decimals=2))
            else:
                cells.append(_fmt(val))
        table.add_row(*cells)

    return table


def _make_totals_row(totals: dict) -> Table:
    """Build a simple 1-row totals summary table."""
    table = Table(title="Season Totals", header_style="bold green", show_header=True)
    keys = [
        ("Goals",       "goals"),
        ("Assists",     "assists"),
        ("Apps",        "appearances"),
        ("Minutes",     "minutes"),
        ("Shots",       "shots_total"),
        ("Key Passes",  "passes_key"),
        ("Tackles",     "tackles_total"),
        ("Interceptions", "tackles_interceptions"),
        ("Yellow Cards", "yellow_cards"),
        ("Penalties",   "penalty_scored"),
        ("Avg Rating",  "avg_rating"),
    ]
    for col_name, _ in keys:
        table.add_column(col_name, justify="right")
    row_cells = []
    for col_name, key in keys:
        val = totals.get(key)
        if key == "avg_rating":
            row_cells.append(_fmt(val, decimals=2))
        else:
            row_cells.append(_fmt(val))
    table.add_row(*row_cells)
    return table


# ── Sub-commands ──────────────────────────────────────────────────────────────

def cmd_player(args: argparse.Namespace) -> None:
    """Show player profile and stats."""
    season = args.season
    db_path = os.getenv("DB_PATH", "playerstats.db")

    with Database(db_path) as db:
        analytics = Analytics(db)
        results = analytics.search_player(args.name, season=season)

    if results.empty:
        console.print(
            f"[yellow]No players found matching '[bold]{args.name}[/bold]'.[/yellow]"
        )
        return

    # If multiple results, let user pick
    if len(results) > 1:
        console.print(f"\nFound [bold]{len(results)}[/bold] players matching '[cyan]{args.name}[/cyan]':\n")
        pick_table = Table(show_header=True, header_style="bold blue")
        pick_table.add_column("#", justify="right", style="dim")
        pick_table.add_column("Name")
        pick_table.add_column("Nationality")
        pick_table.add_column("Position")
        pick_table.add_column("World Cup Team")
        pick_table.add_column("Goals", justify="right")
        pick_table.add_column("Assists", justify="right")
        for i, (_, row) in enumerate(results.iterrows(), start=1):
            pick_table.add_row(
                str(i),
                str(row.get("player_name", "")),
                str(row.get("nationality") or "-"),
                str(row.get("position") or "-"),
                str(row.get("world_cup_team") or "-"),
                _fmt(row.get("goals")),
                _fmt(row.get("assists")),
            )
        console.print(pick_table)
        choice = IntPrompt.ask(
            "Enter number to view details", default=1,
            choices=[str(i) for i in range(1, len(results) + 1)],
        )
        selected = results.iloc[choice - 1]
    else:
        selected = results.iloc[0]

    api_player_id = int(selected["api_player_id"])

    with Database(db_path) as db:
        analytics = Analytics(db)
        report = analytics.player_report(api_player_id, season=season)

    if report["player"] is None:
        console.print("[red]Player not found in database.[/red]")
        return

    console.print()
    console.print(_player_info_panel(report["player"]))

    if report["stats_by_competition"].empty:
        console.print(
            f"[yellow]No stats found for season [bold]{season}[/bold].[/yellow]"
        )
    else:
        console.print()
        console.print(_make_stats_table(report["stats_by_competition"]))
        console.print()
        console.print(_make_totals_row(report["totals"]))

    console.print(f"\n[dim]{DATA_SOURCE_FOOTER.format(season=season)}[/dim]")


def cmd_team(args: argparse.Namespace) -> None:
    """Show team card and squad statistics."""
    season = args.season
    db_path = os.getenv("DB_PATH", "playerstats.db")

    with Database(db_path) as db:
        analytics = Analytics(db)
        teams = analytics.search_team(args.name, season=season)

    if teams.empty:
        console.print(
            f"[yellow]No World Cup teams found matching '[bold]{args.name}[/bold]'.[/yellow]"
        )
        return

    if len(teams) > 1:
        console.print(f"\nFound [bold]{len(teams)}[/bold] teams:\n")
        for i, (_, row) in enumerate(teams.iterrows(), start=1):
            console.print(f"  {i}. {row['name']} ({row.get('country') or '-'})")
        choice = IntPrompt.ask(
            "Enter number", default=1,
            choices=[str(i) for i in range(1, len(teams) + 1)],
        )
        selected = teams.iloc[choice - 1]
    else:
        selected = teams.iloc[0]

    api_team_id = int(selected["api_team_id"])

    with Database(db_path) as db:
        analytics = Analytics(db)
        report = analytics.team_report(api_team_id, season=season)

    if report["team"] is None:
        console.print("[red]Team not found.[/red]")
        return

    team = report["team"]
    team_text = (
        f"[bold]{team.get('name')}[/bold]  |  "
        f"Country: {team.get('country') or '-'}  |  "
        f"World Cup 2026: {'Yes' if team.get('is_world_cup_2026') else 'No'}"
    )
    console.print(Panel(team_text, title="[cyan]Team Info[/cyan]", expand=False))

    squad_df = report["squad_stats"]
    if squad_df.empty:
        console.print(f"[yellow]No squad stats for season {season}.[/yellow]")
    else:
        table = Table(
            title=f"{team.get('name')} — Squad Stats (Season {season})",
            header_style="bold magenta",
            row_styles=["", "dim"],
        )
        cols = [
            ("Player",        "player_name",   "cyan",   "left"),
            ("Pos",           "position",      "white",  "left"),
            ("Age",           "age",           "white",  "right"),
            ("Apps",          "appearances",   "green",  "right"),
            ("Mins",          "minutes",       "green",  "right"),
            ("Goals",         "goals",         "yellow", "right"),
            ("Assists",       "assists",       "yellow", "right"),
            ("Key Passes",    "key_passes",    "white",  "right"),
            ("Tackles",       "tackles",       "blue",   "right"),
            ("Interceptions", "interceptions", "blue",   "right"),
            ("Yellow",        "yellow_cards",  "yellow", "right"),
            ("Red",           "red_cards",     "red",    "right"),
            ("Rating",        "avg_rating",    "cyan",   "right"),
        ]
        for col_name, _, style, justify in cols:
            table.add_column(col_name, style=style, justify=justify)

        for _, row in squad_df.iterrows():
            cells = []
            for col_name, key, _, _ in cols:
                val = row.get(key)
                if key == "avg_rating":
                    cells.append(_fmt(val, decimals=2))
                else:
                    cells.append(_fmt(val))
            table.add_row(*cells)

        console.print(table)

    console.print(f"\n[dim]{DATA_SOURCE_FOOTER.format(season=season)}[/dim]")


def cmd_top(args: argparse.Namespace) -> None:
    """Show top players by a given stat."""
    stat = args.stat
    season = args.season
    position = getattr(args, "position", None)
    limit = args.limit
    world_cup_only = not args.all_players
    db_path = os.getenv("DB_PATH", "playerstats.db")

    if stat not in VALID_STAT_COLUMNS:
        console.print(
            f"[red]Invalid stat '[bold]{stat}[/bold]'.[/red] "
            f"Valid options: {', '.join(sorted(VALID_STAT_COLUMNS))}"
        )
        return

    with Database(db_path) as db:
        analytics = Analytics(db)
        df = analytics.top_players(
            stat=stat,
            season=season,
            position=position,
            limit=limit,
            world_cup_only=world_cup_only,
        )

    if df.empty:
        console.print("[yellow]No data found.[/yellow]")
        return

    stat_label = stat.replace("_", " ").title()
    title = f"Top {limit} — {stat_label} (Season {season}"
    if position:
        title += f", {position}s"
    if world_cup_only:
        title += ", WC 2026 squads"
    title += ")"

    table = Table(title=title, header_style="bold magenta", row_styles=["", "dim"])
    cols = [
        ("#",           "rank",         "dim",    "right"),
        ("Player",      "player_name",  "cyan",   "left"),
        ("Nationality", "nationality",  "white",  "left"),
        ("Position",    "position",     "white",  "left"),
        ("WC Team",     "world_cup_team", "green","left"),
        (stat_label,    stat,           "yellow", "right"),
        ("Goals",       "goals",        "yellow", "right"),
        ("Assists",     "assists",      "yellow", "right"),
        ("Apps",        "appearances",  "white",  "right"),
        ("Avg Rating",  "avg_rating",   "cyan",   "right"),
    ]
    for col_name, _, style, justify in cols:
        table.add_column(col_name, style=style, justify=justify)

    # Which columns need decimals?
    float_cols = {"avg_rating", "rating"}

    for _, row in df.iterrows():
        cells = []
        for col_name, key, _, _ in cols:
            val = row.get(key)
            if key in float_cols or key == stat and stat in {"rating"}:
                cells.append(_fmt(val, decimals=2))
            else:
                cells.append(_fmt(val))
        table.add_row(*cells)

    console.print(table)
    console.print(f"\n[dim]{DATA_SOURCE_FOOTER.format(season=season)}[/dim]")


def cmd_compare(args: argparse.Namespace) -> None:
    """Side-by-side comparison of multiple players."""
    season = args.season
    player_names = [n.strip() for n in args.players.split(",") if n.strip()]
    db_path = os.getenv("DB_PATH", "playerstats.db")

    # Resolve each name to an api_player_id
    resolved_ids: list[int] = []
    with Database(db_path) as db:
        analytics = Analytics(db)
        for name in player_names:
            results = analytics.search_player(name, season=season)
            if results.empty:
                console.print(f"[yellow]Player '[bold]{name}[/bold]' not found — skipping.[/yellow]")
                continue
            if len(results) > 1:
                console.print(f"\nMultiple matches for '[cyan]{name}[/cyan]':")
                for i, (_, row) in enumerate(results.iterrows(), start=1):
                    console.print(
                        f"  {i}. {row['player_name']} "
                        f"({row.get('nationality') or '-'} | {row.get('world_cup_team') or 'No WC team'})"
                    )
                choice = IntPrompt.ask(
                    f"Select player for '{name}'", default=1,
                    choices=[str(i) for i in range(1, len(results) + 1)],
                )
                resolved_ids.append(int(results.iloc[choice - 1]["api_player_id"]))
            else:
                resolved_ids.append(int(results.iloc[0]["api_player_id"]))

    if not resolved_ids:
        console.print("[red]No players resolved for comparison.[/red]")
        return

    with Database(db_path) as db:
        analytics = Analytics(db)
        df = analytics.compare_players(resolved_ids, season=season)

    if df.empty:
        console.print("[yellow]No comparison data found.[/yellow]")
        return

    # Display as a transposed table: rows=stats, cols=players
    stat_rows = [
        ("Nationality",      "nationality",        False),
        ("Position",         "position",           False),
        ("Age",              "age",                False),
        ("World Cup Team",   "world_cup_team",     False),
        ("Appearances",      "appearances",        False),
        ("Minutes",          "minutes",            False),
        ("Goals",            "goals",              False),
        ("Assists",          "assists",            False),
        ("Shots",            "shots_total",        False),
        ("On Target",        "shots_on_target",    False),
        ("Key Passes",       "key_passes",         False),
        ("Pass Accuracy %",  "avg_pass_accuracy",  True),
        ("Tackles",          "tackles",            False),
        ("Interceptions",    "interceptions",      False),
        ("Duels Won %",      "duel_win_pct",       True),
        ("Successful Dribbles", "successful_dribbles", False),
        ("Fouls Committed",  "fouls_committed",    False),
        ("Yellow Cards",     "yellow_cards",       False),
        ("Red Cards",        "red_cards",          False),
        ("Penalties Scored", "penalties_scored",   False),
        ("Saves",            "saves",              False),
        ("Goals Conceded",   "goals_conceded",     False),
        ("Avg Rating",       "avg_rating",         True),
    ]

    table = Table(
        title=f"Player Comparison — Season {season}",
        header_style="bold magenta",
    )
    table.add_column("Statistic", style="cyan", justify="left")
    for _, row in df.iterrows():
        table.add_column(str(row.get("player_name", "?")), justify="right", style="white")

    for stat_label, key, is_float in stat_rows:
        cells = [stat_label]
        for _, row in df.iterrows():
            val = row.get(key)
            if is_float:
                cells.append(_fmt(val, decimals=2))
            else:
                cells.append(_fmt(val))
        table.add_row(*cells)

    console.print(table)
    console.print(f"\n[dim]{DATA_SOURCE_FOOTER.format(season=season)}[/dim]")


# ── Argument parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="report",
        description="World Cup 2026 Player Statistics — Terminal Reports",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # player
    p_player = sub.add_parser("player", help="Show player info and stats")
    p_player.add_argument("name", help="Player name (partial match)")
    p_player.add_argument("--season", type=int, default=2025)

    # team
    p_team = sub.add_parser("team", help="Show team info and squad stats")
    p_team.add_argument("name", help="Team name (partial match)")
    p_team.add_argument("--season", type=int, default=2025)

    # top
    p_top = sub.add_parser("top", help="Show top players by stat")
    p_top.add_argument("--stat", required=True, choices=sorted(VALID_STAT_COLUMNS))
    p_top.add_argument("--season", type=int, default=2025)
    p_top.add_argument(
        "--position",
        choices=["Goalkeeper", "Defender", "Midfielder", "Attacker"],
        default=None,
    )
    p_top.add_argument("--limit", type=int, default=20)
    p_top.add_argument(
        "--all-players",
        action="store_true",
        default=False,
        help="Include players not in WC 2026 squads",
    )

    # compare
    p_cmp = sub.add_parser("compare", help="Side-by-side player comparison")
    p_cmp.add_argument(
        "--players",
        required=True,
        help="Comma-separated player names (e.g. 'Mbappe,Haaland')",
    )
    p_cmp.add_argument("--season", type=int, default=2025)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "player": cmd_player,
        "team": cmd_team,
        "top": cmd_top,
        "compare": cmd_compare,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    try:
        handler(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(0)
    except Exception as exc:
        console.print_exception(show_locals=False)
        sys.exit(1)


if __name__ == "__main__":
    main()
