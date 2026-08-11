"""Constants. No config framework — change values here."""
from pathlib import Path

FORMAT_ID = "gen9championsvgc2026regmb"
SHOWDOWN_MOD = "champions"

# Smogon usage stats. Reg M-B went live 2026-06-17, so 2026-07 is the first
# full month. Bump this as new months publish.
STATS_MONTH = "2026-07"
STATS_CUTOFF = 1760  # lower buckets model ladder noise, not the meta
STATS_URL = (
    f"https://www.smogon.com/stats/{STATS_MONTH}/chaos/{FORMAT_ID}-{STATS_CUTOFF}.json"
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATS_DIR = DATA / "stats"
SHOWDOWN_DIR = DATA / "pokemon-showdown"
CHAMPIONS_DEX = DATA / "champions_dex.json"
DB_PATH = DATA / "pvgc.sqlite3"

# Gauntlet
GAUNTLET_SIZE = 16
HOLDOUT_SIZE = 4  # the proposer never sees these; overfitting shows as a gap
BATTLES_PER_MATCHUP = 50

# Reg M-B rules
TEAM_SIZE = 6
BRING_SIZE = 4
LEVEL = 50
MAX_EVS_TOTAL = 508
MAX_EVS_PER_STAT = 252
