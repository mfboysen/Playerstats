-- World Cup 2026 Player Statistics - DuckDB Star Schema
-- =========================================================

-- -----------------------------------------------------
-- Dimension: dim_date
-- Standard date dimension covering 2024-2027
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_date (
    date_key        INTEGER PRIMARY KEY,   -- YYYYMMDD
    full_date       DATE    NOT NULL,
    year            INTEGER,
    month           INTEGER,
    month_name      VARCHAR,
    quarter         INTEGER,
    week_of_year    INTEGER,
    day_of_week     INTEGER,               -- 0=Monday … 6=Sunday (ISO)
    day_name        VARCHAR,
    is_weekend      BOOLEAN
);

-- -----------------------------------------------------
-- Dimension: dim_competition
-- One row per (league, season) pair
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_competition (
    competition_id  INTEGER PRIMARY KEY,
    api_league_id   INTEGER NOT NULL,
    name            VARCHAR NOT NULL,
    type            VARCHAR,               -- 'League', 'Cup', 'International'
    country         VARCHAR,
    logo_url        VARCHAR,
    season          INTEGER NOT NULL,
    UNIQUE(api_league_id, season)
);

-- -----------------------------------------------------
-- Dimension: dim_team
-- Club and national teams
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_team (
    team_id             INTEGER PRIMARY KEY,
    api_team_id         INTEGER UNIQUE NOT NULL,
    name                VARCHAR NOT NULL,
    short_name          VARCHAR,
    country             VARCHAR,
    logo_url            VARCHAR,
    is_world_cup_2026   BOOLEAN DEFAULT FALSE
);

-- -----------------------------------------------------
-- Dimension: dim_player
-- Player bio information
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_player (
    player_id           INTEGER PRIMARY KEY,
    api_player_id       INTEGER UNIQUE NOT NULL,
    name                VARCHAR NOT NULL,
    firstname           VARCHAR,
    lastname            VARCHAR,
    nationality         VARCHAR,
    birth_date          DATE,
    age                 INTEGER,
    height              VARCHAR,
    weight              VARCHAR,
    position            VARCHAR,           -- Goalkeeper, Defender, Midfielder, Attacker
    photo_url           VARCHAR,
    world_cup_team_id   INTEGER REFERENCES dim_team(team_id)
);

-- -----------------------------------------------------
-- Dimension: dim_match
-- One row per fixture / game
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_match (
    match_id            INTEGER PRIMARY KEY,
    api_fixture_id      INTEGER UNIQUE NOT NULL,
    date_key            INTEGER REFERENCES dim_date(date_key),
    competition_id      INTEGER REFERENCES dim_competition(competition_id),
    home_team_id        INTEGER REFERENCES dim_team(team_id),
    away_team_id        INTEGER REFERENCES dim_team(team_id),
    home_goals          INTEGER,
    away_goals          INTEGER,
    venue               VARCHAR,
    city                VARCHAR,
    round               VARCHAR,
    status              VARCHAR            -- 'Match Finished', 'Not Started', etc.
);

-- -----------------------------------------------------
-- Fact: fact_player_match_stats
-- One row per player per match.
--
-- club_team_id    = the team the player played for in THIS match
-- opponent_team_id= the opposing team in THIS match
-- national_team_id= their WC 2026 national team (denorm for filtering)
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_player_match_stats (
    stat_id                 INTEGER PRIMARY KEY,
    player_id               INTEGER NOT NULL REFERENCES dim_player(player_id),
    match_id                INTEGER NOT NULL REFERENCES dim_match(match_id),
    date_key                INTEGER NOT NULL REFERENCES dim_date(date_key),
    club_team_id            INTEGER NOT NULL REFERENCES dim_team(team_id),
    opponent_team_id        INTEGER NOT NULL REFERENCES dim_team(team_id),
    competition_id          INTEGER NOT NULL REFERENCES dim_competition(competition_id),
    national_team_id        INTEGER REFERENCES dim_team(team_id),

    -- Playing time
    minutes_played          INTEGER DEFAULT 0,
    is_starter              BOOLEAN DEFAULT TRUE,

    -- Attack
    goals                   INTEGER DEFAULT 0,
    assists                 INTEGER DEFAULT 0,
    shots_total             INTEGER DEFAULT 0,
    shots_on_target         INTEGER DEFAULT 0,
    offsides                INTEGER DEFAULT 0,

    -- Passing
    passes_total            INTEGER DEFAULT 0,
    passes_key              INTEGER DEFAULT 0,
    pass_accuracy           DECIMAL(5,2),

    -- Defense
    tackles_total           INTEGER DEFAULT 0,
    tackles_blocks          INTEGER DEFAULT 0,
    tackles_interceptions   INTEGER DEFAULT 0,

    -- Duels
    duels_total             INTEGER DEFAULT 0,
    duels_won               INTEGER DEFAULT 0,

    -- Dribbles
    dribbles_attempts       INTEGER DEFAULT 0,
    dribbles_success        INTEGER DEFAULT 0,
    dribbles_past           INTEGER DEFAULT 0,

    -- Discipline
    fouls_drawn             INTEGER DEFAULT 0,
    fouls_committed         INTEGER DEFAULT 0,
    yellow_cards            INTEGER DEFAULT 0,
    red_cards               INTEGER DEFAULT 0,

    -- Penalties
    penalty_won             INTEGER DEFAULT 0,
    penalty_committed       INTEGER DEFAULT 0,
    penalty_scored          INTEGER DEFAULT 0,
    penalty_missed          INTEGER DEFAULT 0,
    penalty_saved           INTEGER DEFAULT 0,

    -- Goalkeeper
    saves                   INTEGER DEFAULT 0,
    goals_conceded          INTEGER DEFAULT 0,

    -- Rating
    rating                  DECIMAL(4,2),

    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(player_id, match_id)
);

-- -----------------------------------------------------
-- View: v_player_season_summary
-- Aggregates all matches per player per competition/season.
-- Use this for season-level analysis. Filter by national_team_id
-- to scope to World Cup squads.
-- -----------------------------------------------------
CREATE VIEW IF NOT EXISTS v_player_season_summary AS
SELECT
    p.api_player_id,
    p.name                              AS player_name,
    p.nationality,
    p.position,
    nt.name                             AS national_team,
    c.name                              AS competition,
    c.season,
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
    SUM(f.tackles_blocks)               AS blocks,
    SUM(f.duels_total)                  AS duels_total,
    SUM(f.duels_won)                    AS duels_won,
    CASE WHEN SUM(f.duels_total) > 0
         THEN ROUND(100.0 * SUM(f.duels_won) / SUM(f.duels_total), 1)
    END                                 AS duel_win_pct,
    SUM(f.dribbles_attempts)            AS dribble_attempts,
    SUM(f.dribbles_success)             AS successful_dribbles,
    SUM(f.fouls_committed)              AS fouls_committed,
    SUM(f.fouls_drawn)                  AS fouls_drawn,
    SUM(f.yellow_cards)                 AS yellow_cards,
    SUM(f.red_cards)                    AS red_cards,
    SUM(f.penalty_scored)               AS penalties_scored,
    SUM(f.saves)                        AS saves,
    SUM(f.goals_conceded)               AS goals_conceded,
    AVG(f.rating)                       AS avg_rating
FROM fact_player_match_stats f
JOIN dim_player      p  ON f.player_id      = p.player_id
JOIN dim_competition c  ON f.competition_id = c.competition_id
LEFT JOIN dim_team   nt ON f.national_team_id = nt.team_id
GROUP BY
    p.api_player_id, p.name, p.nationality, p.position,
    nt.name, c.name, c.season;

-- -----------------------------------------------------
-- View: v_world_cup_team_stats
-- Aggregate stats per World Cup national team across all
-- club competitions in a given season.
-- -----------------------------------------------------
CREATE VIEW IF NOT EXISTS v_world_cup_team_stats AS
SELECT
    nt.name                             AS national_team,
    nt.country,
    c.season,
    COUNT(DISTINCT p.player_id)         AS squad_size,
    COUNT(DISTINCT f.match_id)          AS total_club_matches,
    AVG(f.rating)                       AS avg_player_rating,
    SUM(f.goals)                        AS total_goals,
    SUM(f.assists)                      AS total_assists,
    SUM(f.minutes_played)               AS total_minutes,
    SUM(f.tackles_total)                AS total_tackles,
    SUM(f.passes_total)                 AS total_passes
FROM fact_player_match_stats f
JOIN dim_player      p  ON f.player_id        = p.player_id
JOIN dim_team        nt ON f.national_team_id = nt.team_id
JOIN dim_competition c  ON f.competition_id   = c.competition_id
WHERE nt.is_world_cup_2026 = TRUE
GROUP BY nt.name, nt.country, c.season;
