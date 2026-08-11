# PVGC Harness & Gauntlet Implementation Plan (M0–M4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local tool that takes a Pokémon Champions Reg M-B team paste and reports what it loses to, with confidence intervals and bring-4 distributions, by simulating battles against a usage-derived gauntlet on a local Pokémon Showdown server.

**Architecture:** A local Showdown server runs battles; poke-env parses battle state and drives a heuristic player with an explicit bring-4 policy. Everything above `score_team()` — the single seam — sees only plain dataclasses, so the engine or the policy can be replaced later without touching the search layer. Gauntlet opponents are sampled from Smogon usage statistics via teammate correlation. Results land in SQLite.

**Tech Stack:** Python 3.12, poke-env 0.15.0, Node 24 + `smogon/pokemon-showdown`, stdlib `sqlite3` / `urllib` / `statistics`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-11-pvgc-champions-team-search-design.md`

**Out of scope for this plan:** the LLM proposer (M5) gets its own plan once M4 produces real numbers.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | deps, pytest config |
| `pvgc/config.py` | constants: format id, stats month, cutoff, paths |
| `pvgc/dex.py` | Champions species/move data; patches poke-env's GenData |
| `pvgc/team.py` | `Mon`/`Team` dataclasses, paste parse/serialize, `validate()` |
| `pvgc/usage.py` | fetch + cache Smogon chaos JSON, typed accessors |
| `pvgc/teamgen.py` | usage stats → gauntlet teams via teammate correlation |
| `pvgc/store.py` | SQLite schema and queries |
| `pvgc/bring.py` | bring-4 selection policy (own file: it is the most likely thing to be replaced) |
| `pvgc/sim.py` | Showdown server lifecycle, poke-env player wiring |
| `pvgc/score.py` | **THE SEAM.** `score_team()` + aggregation math |
| `pvgc/cli.py` | argparse subcommands |
| `scripts/gen_champions_data.py` | extract Champions dex from PS via node |
| `tests/` | pytest suite |

`bring.py` is split from `sim.py` because the spec flags the v1 bring policy as the crudest component on the critical path. Isolating it means replacing it is a one-file change with its own tests.

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`, `pvgc/__init__.py`, `pvgc/config.py`, `tests/__init__.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "pvgc"
version = "0.1.0"
description = "Pokemon Champions VGC team search"
requires-python = ">=3.12"
dependencies = [
    "poke-env==0.15.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.24"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["slow: requires a running Showdown server"]
addopts = "-m 'not slow'"
asyncio_mode = "auto"
```

- [ ] **Step 2: Create the virtualenv and install**

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Expected: installs poke-env 0.15.0 and its deps (gymnasium, numpy, orjson, pettingzoo, requests, tabulate, websockets==16.0).

- [ ] **Step 3: Create `pvgc/config.py`**

```python
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
```

- [ ] **Step 4: Create empty package markers**

```bash
touch pvgc/__init__.py tests/__init__.py
mkdir -p data scripts
```

- [ ] **Step 5: Verify the install imports**

Run: `.venv/bin/python -c "import poke_env, pvgc.config; print(poke_env.__file__); print(pvgc.config.FORMAT_ID)"`
Expected: a path ending in `poke_env/__init__.py`, then `gen9championsvgc2026regmb`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml pvgc/ tests/
git commit -m "chore: scaffold pvgc package with poke-env and pytest"
```

---

## Task 2: M0 spike — verify the format works end to end

This task is a **gate**. It answers four questions and writes them down. Do not proceed to Task 3 until `docs/superpowers/SPIKE-M0.md` exists with all four answered.

**Files:**
- Create: `docs/superpowers/SPIKE-M0.md`, `scripts/spike_battle.py`

- [ ] **Step 1: Clone and build Pokémon Showdown**

```bash
git clone --depth 1 https://github.com/smogon/pokemon-showdown.git data/pokemon-showdown
cd data/pokemon-showdown && npm install && npm run build
```

Expected: build completes, `data/pokemon-showdown/dist/` exists.

- [ ] **Step 2: Confirm the format exists in this checkout**

```bash
cd data/pokemon-showdown && node -e "
const {Dex} = require('./dist/sim/dex');
const f = Dex.formats.get('gen9championsvgc2026regmb');
console.log(JSON.stringify({name: f.name, exists: f.exists, mod: f.mod, ruleset: f.ruleset}, null, 2));
"
```

Expected: `exists: true`, `mod: "champions"`, ruleset including `Flat Rules`, `VGC Timer`, `Open Team Sheets`.

**If `exists` is false**, the checkout predates Reg M-B. Stop and report — everything downstream depends on this.

- [ ] **Step 3: Answer the Mega question**

This is the one genuine unknown in the design: how does a team paste designate which Pokémon may Mega Evolve? In mainline gens it was a held Mega Stone, but Reg M-B enforces Item Clause and allows one Mega per team, so Champions may use a separate field.

```bash
cd data/pokemon-showdown && node -e "
const {Dex} = require('./dist/sim/dex');
const dex = Dex.mod('champions');
const megas = dex.species.all().filter(s => s.isMega || s.forme === 'Mega' || (s.name||'').includes('-Mega'));
console.log('mega count:', megas.length);
console.log(JSON.stringify(megas.slice(0,3).map(s => ({
  name: s.name, baseSpecies: s.baseSpecies, requiredItem: s.requiredItem,
  types: s.types, abilities: s.abilities, baseStats: s.baseStats, isMega: s.isMega
})), null, 2));
"
```

Record in the spike doc: whether Megas require an item (`requiredItem`), and if not, what field or team-paste line designates them.

- [ ] **Step 4: Start the server with security disabled**

```bash
cd data/pokemon-showdown && node pokemon-showdown start --no-security
```

Expected: `Worker 1 now listening on 0.0.0.0:8000`. Leave running in another terminal for the next step.

- [ ] **Step 5: Write the spike script**

Create `scripts/spike_battle.py`:

```python
"""M0 spike: can poke-env run a Reg M-B battle at all?

Run the Showdown server first:
  cd data/pokemon-showdown && node pokemon-showdown start --no-security
"""
import asyncio
import inspect

from poke_env import AccountConfiguration, LocalhostServerConfiguration
from poke_env.player import Player, RandomPlayer

from pvgc.config import FORMAT_ID


def report_api():
    """Record the exact signatures the rest of the plan codes against."""
    print("Player.__init__:", inspect.signature(Player.__init__))
    print("Player.choose_move:", inspect.signature(Player.choose_move))
    print("Player.teampreview:", inspect.signature(Player.teampreview))
    print("Player.battle_against:", inspect.signature(Player.battle_against))


async def main():
    report_api()
    p1 = RandomPlayer(
        account_configuration=AccountConfiguration("pvgcspike1", None),
        server_configuration=LocalhostServerConfiguration,
        battle_format=FORMAT_ID,
        max_concurrent_battles=1,
    )
    p2 = RandomPlayer(
        account_configuration=AccountConfiguration("pvgcspike2", None),
        server_configuration=LocalhostServerConfiguration,
        battle_format=FORMAT_ID,
        max_concurrent_battles=1,
    )
    await p1.battle_against(p2, n_battles=1)
    print("finished:", p1.n_finished_battles, "won:", p1.n_won_battles)
    for tag, battle in p1.battles.items():
        print(tag, "turns:", battle.turn, "won:", battle.won)
        print("team size:", len(battle.team))


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 6: Run the spike**

Run: `.venv/bin/python scripts/spike_battle.py`

Three possible outcomes — record which one happened:

1. **Battle completes.** Note it and proceed. (Unlikely without team data, since RandomPlayer needs a legal team for a VGC format — if it errors on team validation, that is outcome 2, not a failure of the harness.)
2. **Errors on unknown species / KeyError in pokedex.** Expected. `GenData.from_format` does `int(format[3])` → gen 9 → loads stock `gen9pokedex.json`, which has no Champions Megas. Task 3 fixes this.
3. **poke-env's parser cannot represent Mega mechanics in doubles** (protocol desync, crash mid-battle). Stop and reopen the engine decision in favour of `pokemon-showdown simulate-battle`.

- [ ] **Step 7: Write `docs/superpowers/SPIKE-M0.md`**

Must contain, with actual output pasted in:

```markdown
# M0 Spike Findings

- Showdown checkout commit: <sha>
- Format `gen9championsvgc2026regmb` exists: <yes/no>, mod: <value>, ruleset: <value>
- Mega designation mechanism: <requiredItem / other field / team-paste line>
- Mega count in the champions mod: <n>
- poke-env API signatures (pasted from report_api()):
  - Player.__init__: <...>
  - Player.choose_move: <...>
  - Player.teampreview: <...>
- Spike outcome: <1, 2, or 3>
- Decision: <proceed to Task 3 / reopen engine decision>
```

- [ ] **Step 8: Commit**

```bash
git add scripts/spike_battle.py docs/superpowers/SPIKE-M0.md
git commit -m "spike: verify Reg M-B format and poke-env compatibility (M0)"
```

---

## Task 3: Extract Champions dex data

Showdown mods are *deltas* over the base dex. Rather than reimplementing overlay resolution in Python, let Showdown resolve it and emit JSON.

**Files:**
- Create: `scripts/gen_champions_data.py`, `scripts/dump_champions_dex.js`
- Test: `tests/test_dex.py`

- [ ] **Step 1: Write the node dumper**

Create `scripts/dump_champions_dex.js`:

```javascript
// Emits resolved champions-mod dex data as JSON on stdout.
// Run from the pokemon-showdown checkout root.
const {Dex} = require('./dist/sim/dex');
const dex = Dex.mod('champions');

const species = {};
for (const s of dex.species.all()) {
  if (!s.exists || s.isNonstandard) continue;
  species[s.id] = {
    name: s.name,
    baseSpecies: s.baseSpecies,
    forme: s.forme,
    types: s.types,
    baseStats: s.baseStats,
    abilities: s.abilities,
    isMega: !!s.isMega,
    requiredItem: s.requiredItem || null,
    weightkg: s.weightkg,
  };
}

const moves = {};
for (const m of dex.moves.all()) {
  if (!m.exists || m.isNonstandard) continue;
  moves[m.id] = {
    name: m.name, type: m.type, category: m.category,
    basePower: m.basePower, accuracy: m.accuracy, priority: m.priority,
    target: m.target,
  };
}

const learnsets = {};
for (const s of dex.species.all()) {
  if (!s.exists || s.isNonstandard) continue;
  const ls = dex.species.getLearnsetData(s.id);
  if (ls && ls.learnset) learnsets[s.id] = Object.keys(ls.learnset);
}

const items = {};
for (const i of dex.items.all()) {
  if (!i.exists || i.isNonstandard) continue;
  items[i.id] = {name: i.name, megaStone: i.megaStone || null};
}

process.stdout.write(JSON.stringify({species, moves, learnsets, items}));
```

- [ ] **Step 2: Write the Python driver**

Create `scripts/gen_champions_data.py`:

```python
"""Dump the resolved champions-mod dex to data/champions_dex.json."""
import json
import shutil
import subprocess
import sys

from pvgc.config import CHAMPIONS_DEX, SHOWDOWN_DIR

DUMPER = "dump_champions_dex.js"


def main():
    if not SHOWDOWN_DIR.exists():
        sys.exit(f"No Showdown checkout at {SHOWDOWN_DIR}. See Task 2 Step 1.")
    shutil.copy(f"scripts/{DUMPER}", SHOWDOWN_DIR / DUMPER)
    proc = subprocess.run(
        ["node", DUMPER], cwd=SHOWDOWN_DIR, capture_output=True, text=True
    )
    if proc.returncode != 0:
        sys.exit(f"node failed:\n{proc.stderr}")
    data = json.loads(proc.stdout)
    CHAMPIONS_DEX.parent.mkdir(parents=True, exist_ok=True)
    CHAMPIONS_DEX.write_text(json.dumps(data))
    megas = sum(1 for s in data["species"].values() if s["isMega"])
    print(
        f"species={len(data['species'])} moves={len(data['moves'])} "
        f"learnsets={len(data['learnsets'])} items={len(data['items'])} megas={megas}"
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run it**

Run: `.venv/bin/python scripts/gen_champions_data.py`
Expected: a line like `species=1400 moves=900 learnsets=1300 items=250 megas=59`. The mega count must be non-zero — if it is zero, the field detection in `dump_champions_dex.js` is wrong and Step 3 of Task 2 has the answer for what to use instead.

- [ ] **Step 4: Write the failing test**

Create `tests/test_dex.py`:

```python
import pytest

from pvgc import dex


def test_dex_loads_megas():
    d = dex.load()
    megas = [s for s in d.species.values() if s["isMega"]]
    assert len(megas) > 0


def test_known_species_present():
    d = dex.load()
    assert "garchomp" in d.species
    assert d.species["garchomp"]["types"] == ["Dragon", "Ground"]


def test_learnset_lookup():
    d = dex.load()
    assert "earthquake" in d.learnset("garchomp")


def test_unknown_species_raises():
    d = dex.load()
    with pytest.raises(KeyError):
        d.learnset("notarealpokemon")
```

- [ ] **Step 5: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_dex.py -v`
Expected: FAIL — `ModuleNotFoundError` or `AttributeError: module 'pvgc.dex' has no attribute 'load'`

- [ ] **Step 6: Write `pvgc/dex.py`**

```python
"""Champions dex data, loaded from the JSON produced by scripts/gen_champions_data.py."""
import json
from dataclasses import dataclass
from functools import lru_cache

from pvgc.config import CHAMPIONS_DEX


def to_id(s: str) -> str:
    """Showdown's id normalisation: lowercase alphanumerics only."""
    return "".join(c for c in s.lower() if c.isalnum())


@dataclass(frozen=True)
class Dex:
    species: dict
    moves: dict
    learnsets: dict
    items: dict

    def learnset(self, species: str) -> set[str]:
        return set(self.learnsets[to_id(species)])

    def is_mega(self, species: str) -> bool:
        return self.species[to_id(species)]["isMega"]

    def mega_forms(self) -> list[dict]:
        return [s for s in self.species.values() if s["isMega"]]


@lru_cache(maxsize=1)
def load() -> Dex:
    if not CHAMPIONS_DEX.exists():
        raise FileNotFoundError(
            f"{CHAMPIONS_DEX} missing. Run: python scripts/gen_champions_data.py"
        )
    raw = json.loads(CHAMPIONS_DEX.read_text())
    return Dex(
        species=raw["species"],
        moves=raw["moves"],
        learnsets=raw["learnsets"],
        items=raw["items"],
    )
```

- [ ] **Step 7: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_dex.py -v`
Expected: 4 passed

- [ ] **Step 8: Commit**

```bash
git add scripts/dump_champions_dex.js scripts/gen_champions_data.py pvgc/dex.py tests/test_dex.py
git commit -m "feat: extract and load Champions mod dex data"
```

---

## Task 4: Patch poke-env's GenData with Champions species

`GenData.from_format("gen9championsvgc2026regmb")` loads stock gen-9 data. Champions-only Megas are absent, so poke-env will raise on encountering them mid-battle. Merge our data into the cached instance before any player connects.

**Files:**
- Modify: `pvgc/dex.py`
- Test: `tests/test_dex.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dex.py`:

```python
def test_patch_gen_data_adds_champions_species():
    from poke_env.data import GenData

    from pvgc.config import FORMAT_ID

    dex.patch_gen_data()
    gd = GenData.from_format(FORMAT_ID)
    d = dex.load()
    mega_ids = [s for s in d.species if d.species[s]["isMega"]]
    assert mega_ids, "no megas in champions dex"
    missing = [m for m in mega_ids if m not in gd.pokedex]
    assert not missing, f"unpatched megas: {missing[:5]}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_dex.py::test_patch_gen_data_adds_champions_species -v`
Expected: FAIL — `AttributeError: module 'pvgc.dex' has no attribute 'patch_gen_data'`

- [ ] **Step 3: Implement `patch_gen_data`**

Append to `pvgc/dex.py`:

```python
def patch_gen_data() -> None:
    """Merge Champions species/moves into poke-env's cached GenData.

    GenData.from_format does `int(format[3])`, so our format resolves to gen 9
    and loads stock gen-9 JSON with no Champions Megas. poke-env raises on
    unknown species mid-battle, so we merge before any player connects.

    Idempotent: safe to call more than once.
    """
    from poke_env.data import GenData

    from pvgc.config import FORMAT_ID

    d = load()
    gd = GenData.from_format(FORMAT_ID)

    for sid, s in d.species.items():
        if sid in gd.pokedex:
            continue
        gd.pokedex[sid] = {
            "num": 0,
            "name": s["name"],
            "types": s["types"],
            "baseStats": s["baseStats"],
            "abilities": {str(k): v for k, v in enumerate(s["abilities"].values())}
            if isinstance(s["abilities"], dict)
            else s["abilities"],
            "weightkg": s["weightkg"],
            "baseSpecies": s["baseSpecies"],
            "forme": s["forme"],
            "heightm": 1.0,
            "evos": [],
            "prevo": "",
            "eggGroups": [],
        }

    for mid, m in d.moves.items():
        if mid in gd.moves:
            continue
        gd.moves[mid] = {
            "name": m["name"],
            "type": m["type"],
            "category": m["category"],
            "basePower": m["basePower"],
            "accuracy": m["accuracy"],
            "priority": m["priority"],
            "target": m["target"],
            "pp": 10,
            "flags": {},
            "secondary": None,
        }
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_dex.py -v`
Expected: 5 passed

If it fails with a `KeyError` on a field poke-env expects that is not in the dict above, add that field with a neutral default and note it in `SPIKE-M0.md`. The set of required fields is exactly what poke-env's `Pokemon.__init__` reads.

- [ ] **Step 5: Commit**

```bash
git add pvgc/dex.py tests/test_dex.py
git commit -m "feat: patch poke-env GenData with Champions species and moves"
```

---

## Task 5: Team model and paste parsing

**Files:**
- Create: `pvgc/team.py`, `tests/test_team.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_team.py`:

```python
from pvgc.team import Mon, Team

PASTE = """Garchomp @ Life Orb
Ability: Rough Skin
Level: 50
EVs: 4 HP / 252 Atk / 252 Spe
Jolly Nature
- Earthquake
- Dragon Claw
- Protect
- Rock Slide

Rillaboom @ Assault Vest
Ability: Grassy Surge
Level: 50
EVs: 252 HP / 252 Atk / 4 Def
Adamant Nature
- Grassy Glide
- Wood Hammer
- Fake Out
- U-turn
"""


def test_parse_basic():
    team = Team.from_paste(PASTE)
    assert len(team.mons) == 2
    chomp = team.mons[0]
    assert chomp.species == "Garchomp"
    assert chomp.item == "Life Orb"
    assert chomp.ability == "Rough Skin"
    assert chomp.nature == "Jolly"
    assert chomp.level == 50
    assert chomp.evs == {"hp": 4, "atk": 252, "spe": 252}
    assert chomp.moves == ["Earthquake", "Dragon Claw", "Protect", "Rock Slide"]


def test_roundtrip():
    team = Team.from_paste(PASTE)
    assert Team.from_paste(team.to_paste()) == team


def test_hash_is_order_independent():
    team = Team.from_paste(PASTE)
    reversed_team = Team(mons=list(reversed(team.mons)))
    assert team.hash() == reversed_team.hash()


def test_hash_changes_with_item():
    team = Team.from_paste(PASTE)
    other = Team(
        mons=[Mon(**{**team.mons[0].__dict__, "item": "Choice Scarf"})] + team.mons[1:]
    )
    assert team.hash() != other.hash()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_team.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pvgc.team'`

- [ ] **Step 3: Implement `pvgc/team.py`**

```python
"""Team model and Showdown paste serialisation."""
import hashlib
import json
from dataclasses import dataclass, field

from pvgc.config import LEVEL

STAT_KEYS = {"HP": "hp", "Atk": "atk", "Def": "def", "SpA": "spa", "SpD": "spd", "Spe": "spe"}
STAT_ORDER = ["hp", "atk", "def", "spa", "spd", "spe"]
STAT_LABELS = {v: k for k, v in STAT_KEYS.items()}


@dataclass
class Mon:
    species: str
    item: str
    ability: str
    nature: str
    evs: dict[str, int] = field(default_factory=dict)
    ivs: dict[str, int] = field(default_factory=dict)
    moves: list[str] = field(default_factory=list)
    level: int = LEVEL

    def canonical(self) -> tuple:
        return (
            self.species,
            self.item,
            self.ability,
            self.nature,
            tuple(sorted(self.evs.items())),
            tuple(sorted(self.moves)),
        )


@dataclass
class Team:
    mons: list[Mon]

    @classmethod
    def from_paste(cls, paste: str) -> "Team":
        mons = []
        for block in [b for b in paste.strip().split("\n\n") if b.strip()]:
            mons.append(_parse_mon(block))
        return cls(mons=mons)

    def to_paste(self) -> str:
        return "\n\n".join(_render_mon(m) for m in self.mons) + "\n"

    def hash(self) -> str:
        payload = json.dumps(sorted(m.canonical() for m in self.mons), default=list)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def __eq__(self, other) -> bool:
        return isinstance(other, Team) and sorted(
            m.canonical() for m in self.mons
        ) == sorted(m.canonical() for m in other.mons)


def _parse_evs(value: str) -> dict[str, int]:
    out = {}
    for part in value.split("/"):
        part = part.strip()
        if not part:
            continue
        amount, label = part.split(maxsplit=1)
        out[STAT_KEYS[label.strip()]] = int(amount)
    return out


def _parse_mon(block: str) -> Mon:
    lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
    header = lines[0]
    if " @ " in header:
        species, item = header.split(" @ ", 1)
    else:
        species, item = header, ""
    mon = Mon(species=species.strip(), item=item.strip(), ability="", nature="")
    for line in lines[1:]:
        if line.startswith("- "):
            mon.moves.append(line[2:].strip())
        elif line.startswith("Ability: "):
            mon.ability = line[len("Ability: "):].strip()
        elif line.startswith("Level: "):
            mon.level = int(line[len("Level: "):].strip())
        elif line.startswith("EVs: "):
            mon.evs = _parse_evs(line[len("EVs: "):])
        elif line.startswith("IVs: "):
            mon.ivs = _parse_evs(line[len("IVs: "):])
        elif line.endswith(" Nature"):
            mon.nature = line[: -len(" Nature")].strip()
    return mon


def _render_evs(evs: dict[str, int]) -> str:
    parts = [f"{evs[k]} {STAT_LABELS[k]}" for k in STAT_ORDER if evs.get(k)]
    return " / ".join(parts)


def _render_mon(m: Mon) -> str:
    lines = [f"{m.species} @ {m.item}" if m.item else m.species]
    if m.ability:
        lines.append(f"Ability: {m.ability}")
    lines.append(f"Level: {m.level}")
    if m.evs:
        lines.append(f"EVs: {_render_evs(m.evs)}")
    if m.nature:
        lines.append(f"{m.nature} Nature")
    if m.ivs:
        lines.append(f"IVs: {_render_evs(m.ivs)}")
    lines.extend(f"- {mv}" for mv in m.moves)
    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_team.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add pvgc/team.py tests/test_team.py
git commit -m "feat: add Team/Mon model with Showdown paste parsing"
```

---

## Task 6: Team legality validation

A team rejected by Showdown mid-gauntlet corrupts the run silently. This is the gate that prevents it.

**Files:**
- Modify: `pvgc/team.py`
- Test: `tests/test_validate.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_validate.py`:

```python
import copy

import pytest

from pvgc.team import Mon, Team


def _mon(species, item, ability, moves):
    return Mon(
        species=species, item=item, ability=ability, nature="Jolly",
        evs={"hp": 4, "atk": 252, "spe": 252}, moves=moves, level=50,
    )


@pytest.fixture
def legal_team():
    # Six distinct species, six distinct items. Moves must exist in the
    # Champions learnsets; adjust species/moves if the dex disagrees.
    return Team(mons=[
        _mon("Garchomp", "Life Orb", "Rough Skin", ["Earthquake", "Protect"]),
        _mon("Rillaboom", "Assault Vest", "Grassy Surge", ["Wood Hammer", "Fake Out"]),
        _mon("Incineroar", "Sitrus Berry", "Intimidate", ["Flare Blitz", "Fake Out"]),
        _mon("Amoonguss", "Rocky Helmet", "Regenerator", ["Spore", "Protect"]),
        _mon("Dragonite", "Choice Band", "Inner Focus", ["Outrage", "Extreme Speed"]),
        _mon("Tyranitar", "Leftovers", "Sand Stream", ["Rock Slide", "Protect"]),
    ])


def test_legal_team_passes(legal_team):
    assert legal_team.validate() == []


def test_rejects_duplicate_item(legal_team):
    t = copy.deepcopy(legal_team)
    t.mons[1].item = t.mons[0].item
    errs = t.validate()
    assert any("item clause" in e.lower() for e in errs)


def test_rejects_duplicate_species(legal_team):
    t = copy.deepcopy(legal_team)
    t.mons[1].species = t.mons[0].species
    errs = t.validate()
    assert any("species clause" in e.lower() for e in errs)


def test_rejects_ev_total_overflow(legal_team):
    t = copy.deepcopy(legal_team)
    t.mons[0].evs = {"hp": 252, "atk": 252, "spe": 252}
    errs = t.validate()
    assert any("ev total" in e.lower() for e in errs)


def test_rejects_ev_per_stat_overflow(legal_team):
    t = copy.deepcopy(legal_team)
    t.mons[0].evs = {"hp": 300}
    errs = t.validate()
    assert any("per-stat" in e.lower() for e in errs)


def test_rejects_illegal_move(legal_team):
    t = copy.deepcopy(legal_team)
    t.mons[0].moves = ["Blast Burn Supreme"]
    errs = t.validate()
    assert any("move" in e.lower() for e in errs)


def test_rejects_unknown_species(legal_team):
    t = copy.deepcopy(legal_team)
    t.mons[0].species = "Notarealmon"
    errs = t.validate()
    assert any("species" in e.lower() for e in errs)


def test_rejects_wrong_team_size(legal_team):
    t = Team(mons=legal_team.mons[:5])
    errs = t.validate()
    assert any("team size" in e.lower() for e in errs)


def test_rejects_too_many_moves(legal_team):
    t = copy.deepcopy(legal_team)
    t.mons[0].moves = ["Earthquake", "Protect", "Rock Slide", "Dragon Claw", "Swords Dance"]
    errs = t.validate()
    assert any("moves" in e.lower() for e in errs)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_validate.py -v`
Expected: FAIL — `AttributeError: 'Team' object has no attribute 'validate'`

- [ ] **Step 3: Implement `validate`**

Append to `pvgc/team.py`:

```python
def validate(self) -> list[str]:
    """Return a list of rule violations. Empty list means legal.

    Returns all errors rather than raising on the first, so an LLM repair
    prompt can see every problem at once.
    """
    from pvgc.config import LEVEL, MAX_EVS_PER_STAT, MAX_EVS_TOTAL, TEAM_SIZE
    from pvgc.dex import load, to_id

    dex = load()
    errors: list[str] = []

    if len(self.mons) != TEAM_SIZE:
        errors.append(f"team size: expected {TEAM_SIZE}, got {len(self.mons)}")

    species_ids = [to_id(m.species) for m in self.mons]
    if len(set(species_ids)) != len(species_ids):
        errors.append("species clause: duplicate species on team")

    items = [to_id(m.item) for m in self.mons if m.item]
    if len(set(items)) != len(items):
        errors.append("item clause: duplicate items on team")

    for m in self.mons:
        sid = to_id(m.species)
        if sid not in dex.species:
            errors.append(f"species: {m.species!r} not in Reg M-B dex")
            continue

        if m.item and to_id(m.item) not in dex.items:
            errors.append(f"item: {m.item!r} not in Reg M-B dex")

        total = sum(m.evs.values())
        if total > MAX_EVS_TOTAL:
            errors.append(f"EV total: {m.species} has {total} > {MAX_EVS_TOTAL}")
        for stat, val in m.evs.items():
            if val > MAX_EVS_PER_STAT:
                errors.append(
                    f"per-stat EV: {m.species} {stat}={val} > {MAX_EVS_PER_STAT}"
                )

        if not 1 <= len(m.moves) <= 4:
            errors.append(f"moves: {m.species} has {len(m.moves)}, expected 1-4")

        legal = dex.learnset(m.species) if sid in dex.learnsets else set()
        for mv in m.moves:
            if to_id(mv) not in dex.moves:
                errors.append(f"move: {mv!r} does not exist")
            elif legal and to_id(mv) not in legal:
                errors.append(f"move: {m.species} cannot learn {mv!r}")

        if m.level != LEVEL:
            errors.append(f"level: {m.species} is {m.level}, expected {LEVEL}")

    return errors
```

Add `validate` as a method on `Team` (inside the class body, not module level).

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_validate.py -v`
Expected: 9 passed

If `test_legal_team_passes` fails because a species or move in the fixture is not in Reg M-B, replace it with one that is — read the actual names out of `data/champions_dex.json`. Do not weaken the validator to make the fixture pass.

- [ ] **Step 5: Commit**

```bash
git add pvgc/team.py tests/test_validate.py
git commit -m "feat: add Reg M-B team legality validation"
```

---

## Task 7: Usage statistics fetch and parse

**Files:**
- Create: `pvgc/usage.py`, `tests/test_usage.py`, `tests/fixtures/chaos_sample.json`

- [ ] **Step 1: Create the test fixture**

Fetch the real file and trim it to the top 12 species so the fixture stays small:

```bash
.venv/bin/python - <<'EOF'
import json, urllib.request
from pvgc.config import STATS_URL
raw = json.loads(urllib.request.urlopen(STATS_URL).read())
top = sorted(raw["data"].items(), key=lambda kv: -kv[1]["usage"])[:12]
raw["data"] = dict(top)
import pathlib
p = pathlib.Path("tests/fixtures"); p.mkdir(parents=True, exist_ok=True)
(p / "chaos_sample.json").write_text(json.dumps(raw))
print("species:", list(raw["data"])[:5])
EOF
```

Note: `smogon.com` is not in the sandbox network allowlist. This step needs `/sandbox` allowlisting or an explicit override.

- [ ] **Step 2: Write the failing test**

Create `tests/test_usage.py`:

```python
import json
from pathlib import Path

import pytest

from pvgc.usage import Usage

FIXTURE = Path(__file__).parent / "fixtures" / "chaos_sample.json"


@pytest.fixture
def usage():
    return Usage(json.loads(FIXTURE.read_text()))


def test_species_sorted_by_usage(usage):
    names = usage.top_species(5)
    assert len(names) == 5
    rates = [usage.usage_rate(n) for n in names]
    assert rates == sorted(rates, reverse=True)


def test_distributions_are_normalised(usage):
    top = usage.top_species(1)[0]
    for dist in (usage.items(top), usage.abilities(top), usage.moves(top)):
        assert dist
        assert abs(sum(dist.values()) - 1.0) < 1e-6


def test_teammates_excludes_self(usage):
    top = usage.top_species(1)[0]
    assert top not in usage.teammates(top)


def test_spread_parsing(usage):
    top = usage.top_species(1)[0]
    nature, evs = next(iter(usage.spreads(top)))
    assert isinstance(nature, str)
    assert set(evs) == {"hp", "atk", "def", "spa", "spd", "spe"}
    assert sum(evs.values()) <= 508
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_usage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pvgc.usage'`

- [ ] **Step 4: Implement `pvgc/usage.py`**

```python
"""Smogon chaos-JSON usage statistics.

Chaos format: {"info": {...}, "data": {"<Species>": {
    "usage": float, "Abilities": {...}, "Items": {...},
    "Spreads": {"<Nature>:<hp>/<atk>/<def>/<spa>/<spd>/<spe>": weight},
    "Moves": {...}, "Teammates": {...}, "Checks and Counters": {...}}}}
Weights are raw counts, not normalised.
"""
import json
import urllib.request
from dataclasses import dataclass

from pvgc.config import STATS_CUTOFF, STATS_DIR, STATS_MONTH, STATS_URL

STAT_ORDER = ["hp", "atk", "def", "spa", "spd", "spe"]


def _normalise(d: dict[str, float]) -> dict[str, float]:
    total = sum(v for v in d.values() if v > 0)
    if total <= 0:
        return {}
    return {k: v / total for k, v in d.items() if v > 0}


@dataclass
class Usage:
    raw: dict

    @property
    def data(self) -> dict:
        return self.raw["data"]

    def top_species(self, n: int) -> list[str]:
        return [
            name
            for name, _ in sorted(
                self.data.items(), key=lambda kv: -kv[1]["usage"]
            )[:n]
        ]

    def usage_rate(self, species: str) -> float:
        return self.data[species]["usage"]

    def items(self, species: str) -> dict[str, float]:
        return _normalise(self.data[species]["Items"])

    def abilities(self, species: str) -> dict[str, float]:
        return _normalise(self.data[species]["Abilities"])

    def moves(self, species: str) -> dict[str, float]:
        return _normalise(
            {k: v for k, v in self.data[species]["Moves"].items() if k}
        )

    def teammates(self, species: str) -> dict[str, float]:
        mates = {
            k: v
            for k, v in self.data[species]["Teammates"].items()
            if k != species and v > 0 and k in self.data
        }
        return _normalise(mates)

    def spreads(self, species: str) -> dict[tuple[str, tuple[int, ...]], float]:
        """Keyed by (nature, (hp, atk, def, spa, spd, spe))."""
        out = {}
        for key, weight in self.data[species]["Spreads"].items():
            if weight <= 0 or ":" not in key:
                continue
            nature, ev_str = key.split(":", 1)
            try:
                evs = tuple(int(x) for x in ev_str.split("/"))
            except ValueError:
                continue
            if len(evs) != 6 or sum(evs) > 508:
                continue
            out[(nature, evs)] = out.get((nature, evs), 0.0) + weight
        return _normalise(out)


def spread_to_evs(evs: tuple[int, ...]) -> dict[str, int]:
    return {k: v for k, v in zip(STAT_ORDER, evs) if v}


def cache_path():
    return STATS_DIR / STATS_MONTH / f"chaos-{STATS_CUTOFF}.json"


def fetch(force: bool = False) -> tuple[Usage, bool]:
    """Return (usage, from_cache). Never silently serves stale data —
    the caller records from_cache in the run row."""
    path = cache_path()
    if path.exists() and not force:
        return Usage(json.loads(path.read_text())), True
    try:
        raw = json.loads(urllib.request.urlopen(STATS_URL, timeout=60).read())
    except Exception as exc:
        if path.exists():
            return Usage(json.loads(path.read_text())), True
        raise RuntimeError(f"fetch failed and no cache at {path}: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw))
    return Usage(raw), False
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_usage.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add pvgc/usage.py tests/test_usage.py tests/fixtures/chaos_sample.json
git commit -m "feat: fetch and parse Smogon chaos usage statistics"
```

---

## Task 8: Gauntlet team generation

**Files:**
- Create: `pvgc/teamgen.py`, `tests/test_teamgen.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_teamgen.py`:

```python
import json
from pathlib import Path

import pytest

from pvgc.teamgen import generate_gauntlet, generate_team
from pvgc.usage import Usage

FIXTURE = Path(__file__).parent / "fixtures" / "chaos_sample.json"


@pytest.fixture
def usage():
    return Usage(json.loads(FIXTURE.read_text()))


def test_team_has_six_distinct_species(usage):
    team = generate_team(usage, seed=1)
    assert len(team.mons) == 6
    assert len({m.species for m in team.mons}) == 6


def test_items_are_distinct(usage):
    team = generate_team(usage, seed=2)
    items = [m.item for m in team.mons]
    assert len(set(items)) == len(items)


def test_deterministic_for_same_seed(usage):
    assert generate_team(usage, seed=7) == generate_team(usage, seed=7)


def test_different_seeds_differ(usage):
    teams = {generate_team(usage, seed=s).hash() for s in range(8)}
    assert len(teams) > 1


def test_generated_teams_are_legal(usage):
    for seed in range(5):
        team = generate_team(usage, seed=seed)
        assert team.validate() == [], f"seed {seed}: {team.validate()}"


def test_gauntlet_size_and_uniqueness(usage):
    teams = generate_gauntlet(usage, n=6, seed=0)
    assert len(teams) == 6
    assert len({t.hash() for t in teams}) == 6
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_teamgen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pvgc.teamgen'`

- [ ] **Step 3: Implement `pvgc/teamgen.py`**

```python
"""Generate gauntlet teams from usage statistics.

A lead is sampled by usage weight, then five more members are added by
teammate correlation rather than raw usage — that is where the synergy
signal lives. Sets are sampled from each species' own distributions.
"""
import random

from pvgc.config import TEAM_SIZE
from pvgc.dex import load as load_dex
from pvgc.dex import to_id
from pvgc.team import Mon, Team
from pvgc.usage import Usage, spread_to_evs

MAX_ATTEMPTS = 50


def _weighted_choice(rng: random.Random, dist: dict, exclude: set = frozenset()):
    pool = {k: v for k, v in dist.items() if k not in exclude and v > 0}
    if not pool:
        return None
    keys = list(pool)
    return rng.choices(keys, weights=[pool[k] for k in keys], k=1)[0]


def _pick_species(rng: random.Random, usage: Usage, chosen: list[str]) -> str | None:
    if not chosen:
        base = {s: usage.usage_rate(s) for s in usage.data}
        return _weighted_choice(rng, base)
    # Blend teammate correlation across everyone already on the team.
    scores: dict[str, float] = {}
    for member in chosen:
        for mate, weight in usage.teammates(member).items():
            scores[mate] = scores.get(mate, 0.0) + weight
    pick = _weighted_choice(rng, scores, exclude=set(chosen))
    if pick:
        return pick
    # Correlation exhausted (thin data) — fall back to usage.
    return _weighted_choice(
        rng, {s: usage.usage_rate(s) for s in usage.data}, exclude=set(chosen)
    )


def _build_mon(
    rng: random.Random, usage: Usage, species: str, used_items: set[str]
) -> Mon | None:
    dex = load_dex()
    if to_id(species) not in dex.species:
        return None

    item = _weighted_choice(
        rng, usage.items(species), exclude={i for i in usage.items(species) if to_id(i) in used_items}
    )
    if item in (None, "nothing"):
        item = ""

    ability = _weighted_choice(rng, usage.abilities(species)) or ""

    spread = _weighted_choice(rng, usage.spreads(species))
    if spread is None:
        nature, evs = "Serious", {}
    else:
        nature, ev_tuple = spread
        evs = spread_to_evs(ev_tuple)

    legal = dex.learnset(species) if to_id(species) in dex.learnsets else set()
    move_dist = {
        m: w
        for m, w in usage.moves(species).items()
        if to_id(m) in dex.moves and (not legal or to_id(m) in legal)
    }
    moves: list[str] = []
    while len(moves) < 4 and move_dist:
        pick = _weighted_choice(rng, move_dist, exclude=set(moves))
        if pick is None:
            break
        moves.append(pick)
    if not moves:
        return None

    return Mon(
        species=species, item=item or "", ability=ability, nature=nature,
        evs=evs, moves=moves,
    )


def generate_team(usage: Usage, seed: int) -> Team:
    """Deterministic for a given seed. Raises if it cannot build a legal team."""
    rng = random.Random(seed)
    for _ in range(MAX_ATTEMPTS):
        mons: list[Mon] = []
        chosen: list[str] = []
        used_items: set[str] = set()
        for _ in range(TEAM_SIZE):
            species = _pick_species(rng, usage, chosen)
            if species is None:
                break
            mon = _build_mon(rng, usage, species, used_items)
            if mon is None:
                break
            chosen.append(species)
            mons.append(mon)
            if mon.item:
                used_items.add(to_id(mon.item))
        if len(mons) == TEAM_SIZE:
            team = Team(mons=mons)
            if not team.validate():
                return team
    raise RuntimeError(f"could not build a legal team from seed {seed}")


def generate_gauntlet(usage: Usage, n: int, seed: int = 0) -> list[Team]:
    """n distinct legal teams."""
    teams: list[Team] = []
    seen: set[str] = set()
    s = seed
    while len(teams) < n:
        if s > seed + n * 100:
            raise RuntimeError(f"only produced {len(teams)} of {n} distinct teams")
        try:
            team = generate_team(usage, seed=s)
        except RuntimeError:
            s += 1
            continue
        if team.hash() not in seen:
            seen.add(team.hash())
            teams.append(team)
        s += 1
    return teams
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_teamgen.py -v`
Expected: 6 passed

`test_generated_teams_are_legal` is the highest-value test here — it wires the generator to the validator, so a generator bug surfaces as a test failure rather than as a corrupted gauntlet.

- [ ] **Step 5: Commit**

```bash
git add pvgc/teamgen.py tests/test_teamgen.py
git commit -m "feat: generate gauntlet teams via teammate correlation"
```

---

## Task 9: SQLite store

**Files:**
- Create: `pvgc/store.py`, `tests/test_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_store.py`:

```python
import pytest

from pvgc.store import Store
from pvgc.team import Mon, Team


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.sqlite3")


@pytest.fixture
def team():
    return Team(mons=[
        Mon(species=f"Mon{i}", item=f"Item{i}", ability="A", nature="Jolly",
            evs={"spe": 252}, moves=["Tackle"])
        for i in range(6)
    ])


def test_add_team_is_idempotent(store, team):
    a = store.add_team(team, role="candidate", source="test")
    b = store.add_team(team, role="candidate", source="test")
    assert a == b


def test_run_and_matchup_roundtrip(store, team):
    tid = store.add_team(team, role="candidate", source="test")
    oid = store.add_team(
        Team(mons=[Mon(species="Opp", item="I", ability="A", nature="Jolly",
                       evs={}, moves=["Tackle"])]),
        role="gauntlet", source="test",
    )
    run_id = store.start_run(gauntlet_hash="abc", n_battles=10, from_cache=False)
    mid = store.add_matchup(run_id, tid, oid, n=10, wins=7, losses=3)
    rows = store.matchups_for(run_id, tid)
    assert len(rows) == 1
    assert rows[0]["wins"] == 7
    assert rows[0]["n"] == 10
    assert mid > 0


def test_matchup_rejects_inconsistent_counts(store, team):
    tid = store.add_team(team, role="candidate", source="test")
    run_id = store.start_run(gauntlet_hash="abc", n_battles=10, from_cache=False)
    with pytest.raises(ValueError):
        store.add_matchup(run_id, tid, tid, n=10, wins=7, losses=2)


def test_battle_rows_recorded(store, team):
    tid = store.add_team(team, role="candidate", source="test")
    run_id = store.start_run(gauntlet_hash="abc", n_battles=1, from_cache=False)
    mid = store.add_matchup(run_id, tid, tid, n=1, wins=1, losses=0)
    store.add_battle(mid, seed=42, winner="a", turns=12,
                     bring_a=["m1", "m2", "m3", "m4"], bring_b=["m5"])
    battles = store.battles_for(mid)
    assert len(battles) == 1
    assert battles[0]["bring_a"] == ["m1", "m2", "m3", "m4"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pvgc.store'`

- [ ] **Step 3: Implement `pvgc/store.py`**

```python
"""SQLite persistence. Plain SQL, no ORM."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pvgc.config import DB_PATH, FORMAT_ID, STATS_CUTOFF, STATS_MONTH
from pvgc.team import Team

SCHEMA = """
CREATE TABLE IF NOT EXISTS team (
    id INTEGER PRIMARY KEY,
    hash TEXT NOT NULL UNIQUE,
    paste TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('gauntlet', 'candidate')),
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS run (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    format_id TEXT NOT NULL,
    stats_month TEXT NOT NULL,
    cutoff INTEGER NOT NULL,
    gauntlet_hash TEXT NOT NULL,
    n_battles INTEGER NOT NULL,
    stats_from_cache INTEGER NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS matchup (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES run(id),
    candidate_id INTEGER NOT NULL REFERENCES team(id),
    opponent_id INTEGER NOT NULL REFERENCES team(id),
    n INTEGER NOT NULL,
    wins INTEGER NOT NULL,
    losses INTEGER NOT NULL,
    failed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS battle (
    id INTEGER PRIMARY KEY,
    matchup_id INTEGER NOT NULL REFERENCES matchup(id),
    seed INTEGER,
    winner TEXT,
    turns INTEGER,
    bring_a_json TEXT NOT NULL DEFAULT '[]',
    bring_b_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_matchup_run ON matchup(run_id, candidate_id);
CREATE INDEX IF NOT EXISTS idx_battle_matchup ON battle(matchup_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path = DB_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def add_team(self, team: Team, role: str, source: str, meta: dict | None = None) -> int:
        h = team.hash()
        row = self.conn.execute("SELECT id FROM team WHERE hash = ?", (h,)).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO team (hash, paste, role, source, created_at, meta_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, team.to_paste(), role, source, _now(), json.dumps(meta or {})),
        )
        self.conn.commit()
        return cur.lastrowid

    def start_run(self, gauntlet_hash: str, n_battles: int, from_cache: bool,
                  notes: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO run (started_at, format_id, stats_month, cutoff,"
            " gauntlet_hash, n_battles, stats_from_cache, notes)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), FORMAT_ID, STATS_MONTH, STATS_CUTOFF, gauntlet_hash,
             n_battles, int(from_cache), notes),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_matchup(self, run_id: int, candidate_id: int, opponent_id: int,
                    n: int, wins: int, losses: int, failed: int = 0) -> int:
        if wins + losses != n:
            raise ValueError(
                f"matchup integrity: n={n} but wins+losses={wins + losses}. "
                "n must be derived from recorded battles, never requested count."
            )
        cur = self.conn.execute(
            "INSERT INTO matchup (run_id, candidate_id, opponent_id, n, wins,"
            " losses, failed) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, candidate_id, opponent_id, n, wins, losses, failed),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_battle(self, matchup_id: int, seed: int | None, winner: str | None,
                   turns: int | None, bring_a: list, bring_b: list) -> int:
        cur = self.conn.execute(
            "INSERT INTO battle (matchup_id, seed, winner, turns, bring_a_json,"
            " bring_b_json) VALUES (?, ?, ?, ?, ?, ?)",
            (matchup_id, seed, winner, turns, json.dumps(bring_a), json.dumps(bring_b)),
        )
        self.conn.commit()
        return cur.lastrowid

    def matchups_for(self, run_id: int, candidate_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM matchup WHERE run_id = ? AND candidate_id = ?",
            (run_id, candidate_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def battles_for(self, matchup_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM battle WHERE matchup_id = ?", (matchup_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["bring_a"] = json.loads(d.pop("bring_a_json"))
            d["bring_b"] = json.loads(d.pop("bring_b_json"))
            out.append(d)
        return out

    def team_paste(self, team_id: int) -> str:
        return self.conn.execute(
            "SELECT paste FROM team WHERE id = ?", (team_id,)
        ).fetchone()["paste"]
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_store.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add pvgc/store.py tests/test_store.py
git commit -m "feat: add SQLite store with matchup count integrity check"
```

---

## Task 10: Bring-4 policy

The spec flags this as the crudest component on the critical path. It gets its own file and its own tests so replacing it is contained.

**Files:**
- Create: `pvgc/bring.py`, `tests/test_bring.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bring.py`:

```python
import itertools

from pvgc.bring import choose_bring, type_advantage
from pvgc.team import Mon, Team


def _team(species: list[str]) -> Team:
    return Team(mons=[
        Mon(species=s, item=f"Item{i}", ability="A", nature="Jolly",
            evs={"spe": 252}, moves=["Tackle"])
        for i, s in enumerate(species)
    ])


def test_returns_four_distinct_indices():
    ours = _team(["Garchomp", "Rillaboom", "Incineroar", "Amoonguss",
                  "Dragonite", "Tyranitar"])
    theirs = _team(["Rillaboom", "Incineroar", "Amoonguss", "Dragonite",
                    "Tyranitar", "Garchomp"])
    picks = choose_bring(ours, theirs)
    assert len(picks) == 4
    assert len(set(picks)) == 4
    assert all(0 <= p < 6 for p in picks)


def test_deterministic():
    ours = _team(["Garchomp", "Rillaboom", "Incineroar", "Amoonguss",
                  "Dragonite", "Tyranitar"])
    theirs = _team(["Tyranitar"] * 1 + ["Amoonguss", "Dragonite", "Garchomp",
                                        "Rillaboom", "Incineroar"])
    assert choose_bring(ours, theirs) == choose_bring(ours, theirs)


def test_considers_all_fifteen_combinations():
    combos = list(itertools.combinations(range(6), 4))
    assert len(combos) == 15


def test_type_advantage_prefers_super_effective():
    # Ground beats Electric; Electric does not beat Ground.
    assert type_advantage(["Ground"], ["Electric"]) > type_advantage(
        ["Electric"], ["Ground"]
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_bring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pvgc.bring'`

- [ ] **Step 3: Implement `pvgc/bring.py`**

```python
"""Bring-4-of-6 selection.

ponytail: crude type-and-speed heuristic over all 15 combinations. This is
the weakest link in fitness validity and is deliberately isolated so it can
be replaced (by a learned preview policy) without touching sim.py.

Open Team Sheets is in the Reg M-B ruleset, so reading the opponent's full
team here is legal, not cheating.
"""
import itertools

from pvgc.config import BRING_SIZE
from pvgc.dex import load, to_id
from pvgc.team import Team


def _typechart() -> dict:
    from poke_env.data import GenData

    from pvgc.config import FORMAT_ID

    return GenData.from_format(FORMAT_ID).type_chart


def type_advantage(attacker_types: list[str], defender_types: list[str]) -> float:
    """Best single-type multiplier the attacker's STAB can achieve."""
    chart = _typechart()
    best = 0.0
    for atk in attacker_types:
        mult = 1.0
        for dfn in defender_types:
            entry = chart.get(dfn.upper(), {})
            mult *= entry.get(atk.upper(), 1.0)
        best = max(best, mult)
    return best


def _types(species: str) -> list[str]:
    dex = load()
    sid = to_id(species)
    if sid not in dex.species:
        return ["Normal"]
    return dex.species[sid]["types"]


def _speed(species: str) -> int:
    dex = load()
    sid = to_id(species)
    if sid not in dex.species:
        return 0
    return dex.species[sid]["baseStats"]["spe"]


def _mon_score(species: str, opponent: Team) -> float:
    mine = _types(species)
    offence = sum(type_advantage(mine, _types(o.species)) for o in opponent.mons)
    defence = sum(type_advantage(_types(o.species), mine) for o in opponent.mons)
    speed = _speed(species) / 200.0
    return offence - defence + speed


def choose_bring(ours: Team, theirs: Team) -> tuple[int, ...]:
    """Indices into ours.mons. Deterministic: ties break on index order."""
    scores = [_mon_score(m.species, theirs) for m in ours.mons]
    best = max(
        itertools.combinations(range(len(ours.mons)), BRING_SIZE),
        key=lambda combo: (sum(scores[i] for i in combo), tuple(-i for i in combo)),
    )
    return tuple(best)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_bring.py -v`
Expected: 4 passed

If `type_advantage` fails because poke-env's type chart keys are not uppercase, print `GenData.from_format(FORMAT_ID).type_chart` and adjust the casing. Do not weaken the assertion.

- [ ] **Step 5: Commit**

```bash
git add pvgc/bring.py tests/test_bring.py
git commit -m "feat: add bring-4 selection policy"
```

---

## Task 11: Scoring aggregation math

Pure functions, no simulator. This is where confidence intervals and failed-battle exclusion live, so they are testable against hand-computed numbers.

**Files:**
- Create: `pvgc/score.py`, `tests/test_score.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_score.py`:

```python
import pytest

from pvgc.score import GauntletResult, MatchupResult, wilson_interval


def test_wilson_midpoint_near_ratio():
    lo, hi = wilson_interval(50, 100)
    assert lo < 0.5 < hi
    assert 0.34 < lo < 0.42
    assert 0.58 < hi < 0.66


def test_wilson_zero_battles_is_full_range():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_narrows_with_more_battles():
    narrow = wilson_interval(500, 1000)
    wide = wilson_interval(5, 10)
    assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])


def test_matchup_winrate_excludes_failed():
    m = MatchupResult(opponent_id=1, n=8, wins=6, losses=2, failed=2)
    assert m.winrate == 0.75
    assert m.n == 8


def test_matchup_rejects_bad_counts():
    with pytest.raises(ValueError):
        MatchupResult(opponent_id=1, n=10, wins=6, losses=2, failed=0)


def test_gauntlet_splits_train_and_holdout():
    matchups = [
        MatchupResult(opponent_id=i, n=10, wins=w, losses=10 - w, failed=0)
        for i, w in enumerate([8, 8, 8, 8, 2, 2])
    ]
    result = GauntletResult(
        candidate_hash="x", matchups=matchups, holdout_ids={4, 5}
    )
    assert result.train_winrate == pytest.approx(0.8)
    assert result.holdout_winrate == pytest.approx(0.2)
    assert result.overfit_gap == pytest.approx(0.6)


def test_gauntlet_overall_pools_battles():
    matchups = [
        MatchupResult(opponent_id=0, n=10, wins=10, losses=0, failed=0),
        MatchupResult(opponent_id=1, n=90, wins=0, losses=90, failed=0),
    ]
    result = GauntletResult(candidate_hash="x", matchups=matchups, holdout_ids=set())
    assert result.overall_winrate == pytest.approx(0.1)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_score.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pvgc.score'`

- [ ] **Step 3: Implement the aggregation half of `pvgc/score.py`**

```python
"""THE SEAM.

Everything above this module sees only the dataclasses defined here. No
poke-env type crosses this boundary, so the engine below (or the policy
driving it) can be replaced without touching the search layer.
"""
import math
from dataclasses import dataclass, field

Z = 1.96  # 95%


def wilson_interval(wins: int, n: int, z: float = Z) -> tuple[float, float]:
    """Wilson score interval. Correct near 0 and 1, unlike the normal
    approximation, which matters for lopsided matchups."""
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


@dataclass(frozen=True)
class MatchupResult:
    opponent_id: int
    n: int
    wins: int
    losses: int
    failed: int = 0
    brings: list[tuple[int, ...]] = field(default_factory=list)

    def __post_init__(self):
        if self.wins + self.losses != self.n:
            raise ValueError(
                f"n={self.n} but wins+losses={self.wins + self.losses}. "
                "n is derived from recorded battles, never the requested count."
            )

    @property
    def winrate(self) -> float:
        return self.wins / self.n if self.n else 0.0

    @property
    def interval(self) -> tuple[float, float]:
        return wilson_interval(self.wins, self.n)

    def bring_distribution(self) -> dict[tuple[int, ...], int]:
        counts: dict[tuple[int, ...], int] = {}
        for b in self.brings:
            counts[b] = counts.get(b, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


@dataclass(frozen=True)
class GauntletResult:
    candidate_hash: str
    matchups: list[MatchupResult]
    holdout_ids: set[int]

    def _pool(self, matchups) -> float:
        n = sum(m.n for m in matchups)
        w = sum(m.wins for m in matchups)
        return w / n if n else 0.0

    @property
    def train(self) -> list[MatchupResult]:
        return [m for m in self.matchups if m.opponent_id not in self.holdout_ids]

    @property
    def holdout(self) -> list[MatchupResult]:
        return [m for m in self.matchups if m.opponent_id in self.holdout_ids]

    @property
    def overall_winrate(self) -> float:
        return self._pool(self.matchups)

    @property
    def train_winrate(self) -> float:
        return self._pool(self.train)

    @property
    def holdout_winrate(self) -> float:
        return self._pool(self.holdout)

    @property
    def overfit_gap(self) -> float:
        """train - holdout. A large positive gap means the search is fitting
        the pool rather than finding a good team."""
        return self.train_winrate - self.holdout_winrate

    @property
    def overall_interval(self) -> tuple[float, float]:
        return wilson_interval(
            sum(m.wins for m in self.matchups), sum(m.n for m in self.matchups)
        )

    @property
    def total_failed(self) -> int:
        return sum(m.failed for m in self.matchups)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_score.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add pvgc/score.py tests/test_score.py
git commit -m "feat: add gauntlet scoring with Wilson intervals and holdout split"
```

---

## Task 12: Showdown server lifecycle and battle runner

**Files:**
- Create: `pvgc/sim.py`
- Test: `tests/test_sim.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_sim.py`:

```python
import pytest

from pvgc.sim import ShowdownServer, _preview_command
from pvgc.team import Mon, Team


def _team(n=6):
    return Team(mons=[
        Mon(species=f"Mon{i}", item=f"Item{i}", ability="A", nature="Jolly",
            evs={"spe": 252}, moves=["Tackle"])
        for i in range(n)
    ])


def test_preview_command_is_one_indexed():
    assert _preview_command((0, 2, 3, 5)) == "/team 1346"


def test_preview_command_length():
    assert len(_preview_command((0, 1, 2, 3)).split()[1]) == 4


@pytest.mark.slow
async def test_single_battle_completes():
    """Requires: cd data/pokemon-showdown && node pokemon-showdown start --no-security"""
    from pvgc.sim import run_battles

    a, b = _real_team_a(), _real_team_b()
    results = await run_battles(a, b, n=1)
    assert len(results) == 1
    assert results[0].winner in ("a", "b", None)


def _real_team_a():
    from pvgc.teamgen import generate_team
    from pvgc.usage import fetch
    return generate_team(fetch()[0], seed=1)


def _real_team_b():
    from pvgc.teamgen import generate_team
    from pvgc.usage import fetch
    return generate_team(fetch()[0], seed=2)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_sim.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pvgc.sim'`

- [ ] **Step 3: Implement `pvgc/sim.py`**

```python
"""Showdown server lifecycle and battle execution."""
import asyncio
import subprocess
import time
from dataclasses import dataclass

from poke_env import AccountConfiguration, LocalhostServerConfiguration
from poke_env.player import SimpleHeuristicsPlayer
from poke_env.teambuilder import ConstantTeambuilder

from pvgc import dex
from pvgc.bring import choose_bring
from pvgc.config import BRING_SIZE, FORMAT_ID, SHOWDOWN_DIR
from pvgc.team import Team

BATTLE_TIMEOUT = 180  # seconds; a hang must not stall the whole gauntlet


def _preview_command(indices: tuple[int, ...]) -> str:
    """Showdown team preview is 1-indexed."""
    return "/team " + "".join(str(i + 1) for i in indices)


@dataclass
class BattleOutcome:
    winner: str | None  # "a", "b", or None for a failure
    turns: int | None
    bring_a: tuple[int, ...] | None
    bring_b: tuple[int, ...] | None
    failed: bool = False


class ShowdownServer:
    """Context manager for a local Showdown server."""

    def __init__(self, port: int = 8000):
        self.port = port
        self.proc: subprocess.Popen | None = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            ["node", "pokemon-showdown", "start", "--no-security", str(self.port)],
            cwd=SHOWDOWN_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if "listening on" in line:
                return self
            if self.proc.poll() is not None:
                raise RuntimeError("Showdown server exited during startup")
        raise TimeoutError("Showdown server did not start within 60s")

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=10)


class GauntletPlayer(SimpleHeuristicsPlayer):
    """SimpleHeuristicsPlayer with an explicit bring-4 policy.

    The base class has no meaningful team-preview logic. Without this
    override the 6-mon design space collapses to 4 and every result is
    meaningless.
    """

    def __init__(self, *args, our_team: Team, **kwargs):
        super().__init__(*args, **kwargs)
        self.our_team = our_team
        self.last_bring: tuple[int, ...] | None = None

    def teampreview(self, battle) -> str:
        opponent = Team(mons=[
            m for m in self._opponent_team_from_battle(battle)
        ])
        if len(opponent.mons) < 1:
            self.last_bring = tuple(range(BRING_SIZE))
            return _preview_command(self.last_bring)
        self.last_bring = choose_bring(self.our_team, opponent)
        return _preview_command(self.last_bring)

    @staticmethod
    def _opponent_team_from_battle(battle):
        """Open Team Sheets means the opponent's full team is visible at
        preview, so this is legal information."""
        from pvgc.team import Mon

        return [
            Mon(species=p.species, item="", ability="", nature="",
                evs={}, moves=[])
            for p in battle.opponent_team.values()
        ]


def _make_player(name: str, team: Team) -> GauntletPlayer:
    dex.patch_gen_data()
    return GauntletPlayer(
        account_configuration=AccountConfiguration(name, None),
        server_configuration=LocalhostServerConfiguration,
        battle_format=FORMAT_ID,
        team=ConstantTeambuilder(team.to_paste()),
        max_concurrent_battles=1,
        our_team=team,
    )


async def run_battles(team_a: Team, team_b: Team, n: int,
                      tag: str = "pvgc") -> list[BattleOutcome]:
    """Run n battles. A battle that fails is recorded as failed=True and
    MUST be excluded from the denominator by the caller."""
    a = _make_player(f"{tag}a{int(time.time()) % 100000}", team_a)
    b = _make_player(f"{tag}b{int(time.time()) % 100000}", team_b)

    outcomes: list[BattleOutcome] = []
    for _ in range(n):
        before = a.n_finished_battles
        try:
            await asyncio.wait_for(
                a.battle_against(b, n_battles=1), timeout=BATTLE_TIMEOUT
            )
        except (asyncio.TimeoutError, Exception):
            outcomes.append(
                BattleOutcome(winner=None, turns=None, bring_a=None,
                              bring_b=None, failed=True)
            )
            continue
        if a.n_finished_battles == before:
            outcomes.append(
                BattleOutcome(winner=None, turns=None, bring_a=None,
                              bring_b=None, failed=True)
            )
            continue
        battle = list(a.battles.values())[-1]
        outcomes.append(
            BattleOutcome(
                winner="a" if battle.won else "b",
                turns=battle.turn,
                bring_a=a.last_bring,
                bring_b=b.last_bring,
            )
        )
    return outcomes
```

- [ ] **Step 4: Run the fast tests**

Run: `.venv/bin/pytest tests/test_sim.py -v`
Expected: 2 passed, 1 deselected (the slow one)

- [ ] **Step 5: Run the slow integration test with the server up**

In one terminal: `cd data/pokemon-showdown && node pokemon-showdown start --no-security`
In another: `.venv/bin/pytest tests/test_sim.py -v -m slow`
Expected: 1 passed. This is the first real Reg M-B battle — if it fails, the error is the most informative artifact in the project so far. Record it in `SPIKE-M0.md`.

- [ ] **Step 6: Commit**

```bash
git add pvgc/sim.py tests/test_sim.py
git commit -m "feat: add Showdown server lifecycle and bring-aware battle runner"
```

---

## Task 13: Wire score_team to the simulator

**Files:**
- Modify: `pvgc/score.py`
- Test: `tests/test_score.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_score.py`:

```python
import asyncio

from pvgc.sim import BattleOutcome


def test_score_team_excludes_failed_battles(monkeypatch):
    from pvgc import score as score_mod
    from pvgc.team import Mon, Team

    def _team(tag):
        return Team(mons=[
            Mon(species=f"{tag}{i}", item=f"I{tag}{i}", ability="A",
                nature="Jolly", evs={}, moves=["Tackle"])
            for i in range(6)
        ])

    async def fake_run_battles(team_a, team_b, n, tag="pvgc"):
        return [
            BattleOutcome(winner="a", turns=5, bring_a=(0, 1, 2, 3), bring_b=(0, 1, 2, 3)),
            BattleOutcome(winner="b", turns=5, bring_a=(0, 1, 2, 3), bring_b=(0, 1, 2, 3)),
            BattleOutcome(winner=None, turns=None, bring_a=None, bring_b=None, failed=True),
        ]

    monkeypatch.setattr(score_mod, "run_battles", fake_run_battles)

    result = asyncio.run(
        score_mod.score_team(_team("c"), [_team("g")], holdout_ids=set(), n=3)
    )
    m = result.matchups[0]
    assert m.n == 2, "failed battle must not enter the denominator"
    assert m.wins == 1 and m.losses == 1
    assert m.failed == 1
    assert result.total_failed == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_score.py::test_score_team_excludes_failed_battles -v`
Expected: FAIL — `AttributeError: module 'pvgc.score' has no attribute 'score_team'`

- [ ] **Step 3: Implement `score_team`**

Append to `pvgc/score.py`:

```python
from pvgc.sim import run_battles  # noqa: E402
from pvgc.team import Team  # noqa: E402


async def score_team(
    candidate: Team,
    gauntlet: list[Team],
    holdout_ids: set[int],
    n: int,
) -> GauntletResult:
    """THE SEAM. Score a candidate against the gauntlet.

    opponent_id is the index into `gauntlet`. Failed battles never enter
    the denominator — a battle that did not happen is not a battle.
    """
    matchups: list[MatchupResult] = []
    for idx, opponent in enumerate(gauntlet):
        outcomes = await run_battles(candidate, opponent, n=n, tag=f"g{idx}")
        wins = sum(1 for o in outcomes if o.winner == "a")
        losses = sum(1 for o in outcomes if o.winner == "b")
        failed = sum(1 for o in outcomes if o.failed)
        brings = [o.bring_a for o in outcomes if o.bring_a is not None]
        matchups.append(
            MatchupResult(
                opponent_id=idx, n=wins + losses, wins=wins, losses=losses,
                failed=failed, brings=brings,
            )
        )
    return GauntletResult(
        candidate_hash=candidate.hash(), matchups=matchups, holdout_ids=holdout_ids
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_score.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add pvgc/score.py tests/test_score.py
git commit -m "feat: wire score_team to the simulator with failed-battle exclusion"
```

---

## Task 14: CLI — the M4 deliverable

**Files:**
- Create: `pvgc/cli.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Implement `pvgc/cli.py`**

```python
"""Command line interface.

  pvgc gauntlet          build and store the gauntlet pool
  pvgc score <paste>     score a team paste against the gauntlet
"""
import argparse
import asyncio
import sys
from pathlib import Path

from pvgc.config import BATTLES_PER_MATCHUP, GAUNTLET_SIZE, HOLDOUT_SIZE
from pvgc.score import score_team
from pvgc.sim import ShowdownServer
from pvgc.store import Store
from pvgc.team import Team
from pvgc.teamgen import generate_gauntlet
from pvgc.usage import fetch


def _load_gauntlet() -> tuple[list[Team], bool]:
    usage, from_cache = fetch()
    return generate_gauntlet(usage, n=GAUNTLET_SIZE, seed=0), from_cache


def cmd_gauntlet(args) -> int:
    teams, from_cache = _load_gauntlet()
    store = Store()
    for i, t in enumerate(teams):
        store.add_team(t, role="gauntlet", source="usage")
        marker = " [holdout]" if i >= GAUNTLET_SIZE - HOLDOUT_SIZE else ""
        print(f"{i:2d} {t.hash()}{marker}  "
              f"{', '.join(m.species for m in t.mons)}")
    print(f"\nstats from cache: {from_cache}")
    return 0


def cmd_score(args) -> int:
    paste = Path(args.paste).read_text()
    team = Team.from_paste(paste)
    errors = team.validate()
    if errors:
        print("Illegal team:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    gauntlet, from_cache = _load_gauntlet()
    holdout_ids = set(range(GAUNTLET_SIZE - HOLDOUT_SIZE, GAUNTLET_SIZE))

    store = Store()
    candidate_id = store.add_team(team, role="candidate", source="manual")
    gauntlet_hash = "".join(t.hash()[:4] for t in gauntlet)
    run_id = store.start_run(
        gauntlet_hash=gauntlet_hash, n_battles=args.n, from_cache=from_cache
    )

    with ShowdownServer():
        result = asyncio.run(
            score_team(team, gauntlet, holdout_ids=holdout_ids, n=args.n)
        )

    for m in result.matchups:
        store.add_matchup(
            run_id, candidate_id,
            store.add_team(gauntlet[m.opponent_id], role="gauntlet", source="usage"),
            n=m.n, wins=m.wins, losses=m.losses, failed=m.failed,
        )

    print(f"\nCandidate {result.candidate_hash}   run {run_id}\n")
    print(f"{'opp':>3}  {'winrate':>8}  {'95% CI':>16}  {'n':>4}  set  opponent")
    for m in result.matchups:
        lo, hi = m.interval
        tag = "hold" if m.opponent_id in holdout_ids else "train"
        species = ", ".join(
            s.species for s in gauntlet[m.opponent_id].mons[:3]
        )
        print(f"{m.opponent_id:3d}  {m.winrate:7.1%}  "
              f"[{lo:5.1%}, {hi:5.1%}]  {m.n:4d}  {tag}  {species}...")

    lo, hi = result.overall_interval
    print(f"\noverall  {result.overall_winrate:.1%}  [{lo:.1%}, {hi:.1%}]")
    print(f"train    {result.train_winrate:.1%}")
    print(f"holdout  {result.holdout_winrate:.1%}")
    print(f"overfit gap {result.overfit_gap:+.1%}")
    if result.total_failed:
        print(f"\nWARNING: {result.total_failed} battles failed and were excluded")

    print("\nbring distribution (top 3 per matchup):")
    for m in result.matchups:
        dist = list(m.bring_distribution().items())[:3]
        pretty = ", ".join(
            f"{'/'.join(team.mons[i].species for i in combo)} x{c}"
            for combo, c in dist
        )
        print(f"  {m.opponent_id:3d}  {pretty}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="pvgc")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("gauntlet", help="build and store the gauntlet pool")

    p_score = sub.add_parser("score", help="score a team paste")
    p_score.add_argument("paste", help="path to a Showdown team paste file")
    p_score.add_argument("-n", type=int, default=BATTLES_PER_MATCHUP)

    args = parser.parse_args()
    return {"gauntlet": cmd_gauntlet, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Register the entry point**

Add to `pyproject.toml`:

```toml
[project.scripts]
pvgc = "pvgc.cli:main"
```

- [ ] **Step 3: Reinstall and build the gauntlet**

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pvgc gauntlet
```

Expected: 16 lines, each with an index, a 16-char hash, six species names, and the last four marked `[holdout]`.

- [ ] **Step 4: Score a real team**

```bash
.venv/bin/pvgc score my_team.txt -n 10
```

Use a small `-n` first. Expected: a per-matchup table with win rates and 95% intervals, overall/train/holdout figures, an overfit gap, and a bring distribution.

- [ ] **Step 5: Full test suite**

Run: `.venv/bin/pytest -v`
Expected: all fast tests pass.

- [ ] **Step 6: Commit**

```bash
git add pvgc/cli.py pyproject.toml
git commit -m "feat: add pvgc CLI with gauntlet and score commands"
```

---

## Task 15: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

```markdown
# pvgc

Team search for Pokémon Champions VGC 2026 Regulation M-B
(`gen9championsvgc2026regmb`), driven by local Pokémon Showdown simulation.

## Setup

    python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
    git clone --depth 1 https://github.com/smogon/pokemon-showdown.git data/pokemon-showdown
    cd data/pokemon-showdown && npm install && npm run build && cd -
    .venv/bin/python scripts/gen_champions_data.py

## Use

    .venv/bin/pvgc gauntlet            # build the opponent pool
    .venv/bin/pvgc score team.txt      # score a team paste

`score` reports per-matchup win rates with 95% Wilson intervals, an
overall figure, and a train/holdout split. A large positive overfit gap
means results are fitting the gauntlet rather than measuring team quality.

## Reading the numbers

At the default 50 battles per matchup, individual cells carry roughly ±7%
standard error — **individual matchup cells are noisy, the overall figure
is not**. Two teams within ~3% overall are indistinguishable.

Fitness is measured under a heuristic policy with a crude bring-4 rule
(`pvgc/bring.py`). "Best team" means "best when played by this agent
against this pool", not "best in the hands of a strong human".

## Notes

- `smogon.com` must be reachable to refresh usage statistics.
- Reg M-B runs 2026-06-17 to 2026-09-09. Retargeting is a change to
  `pvgc/config.py` plus a fresh stats month.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with setup and result interpretation"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Target format constants | 1 |
| M0 spike gate | 2 |
| Champions dex extraction | 3 |
| GenData patching (gen-9 fallback problem) | 4 |
| Team model + paste | 5 |
| Legality validation (all listed rules) | 6 |
| Usage fetch/cache, no silent staleness | 7 |
| Teammate-correlation gauntlet generation | 8 |
| SQLite schema, four tables, count integrity | 9 |
| Bring-4 policy, 15 combinations | 10 |
| Wilson intervals, train/holdout, overfit gap | 11 |
| Server lifecycle, failure handling, timeouts | 12 |
| `score_team` seam, failed-battle exclusion | 13 |
| M4 deliverable (paste → matchup table) | 14 |
| Result interpretation guidance | 15 |

**Gaps deliberately left:** the LLM proposer (M5) is out of scope for this plan, as stated in the header. Adaptive battle allocation and paired seeds are spec'd as upgrade paths, not v1 — not implemented, correctly.

**Type consistency:** `Team.hash()`, `Team.validate()`, `Team.to_paste()`, `Team.from_paste()` used consistently across Tasks 5–14. `MatchupResult(opponent_id, n, wins, losses, failed, brings)` and `GauntletResult(candidate_hash, matchups, holdout_ids)` match between Tasks 11 and 13. `run_battles(team_a, team_b, n, tag)` and `BattleOutcome(winner, turns, bring_a, bring_b, failed)` match between Tasks 12 and 13. `to_id()` and `load()` from `pvgc.dex` used consistently from Task 3 onward.

**Known risk carried into execution:** Tasks 12–14 code against poke-env API signatures that Task 2 Step 5 verifies (`Player.teampreview`, `battle.opponent_team`, `battle.won`, `battle.turn`). If M0 reports different signatures, fix Task 12 before running it — that is exactly what the spike exists to catch.
