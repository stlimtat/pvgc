# M0 Spike Findings

**Date:** 2026-08-12
**Outcome:** **PASS** — proceed with the poke-env + local Showdown server architecture.

## Environment

- Showdown checkout commit: `48767e4`
- poke-env: 0.15.0
- Python 3.12, Node 24.19.0

## Format

`Dex.formats.get('gen9championsvgc2026regmb')`:

```json
{
  "name": "[Gen 9 Champions] VGC 2026 Reg M-B",
  "exists": true,
  "mod": "champions",
  "ruleset": ["Flat Rules", "VGC Timer", "Open Team Sheets"]
}
```

Resolved rule table:

| Rule | Value |
|---|---|
| `evLimit` | **66** |
| max per stat | **32** (hardcoded in `team-validator.js:1102`) |
| `adjustLevel` | 50 |
| `pickedTeamSize` | 4 |
| `maxTeamSize` | 6 |
| Item Clause | enforced |
| Species Clause | enforced |
| Nickname Clause | enforced |
| format banlist | empty (legality comes from mod `isNonstandard` flags) |

## Finding 1: Champions uses Stat Points, not EVs — CRITICAL

The spec and plan both assumed mainline EVs (508 total, 252 per stat). **Wrong.**
Pokémon Champions uses Stat Points: **66 total, 32 per stat**.

Showdown's own TeamValidator, on a team built with mainline EVs:

```
Venusaur has more than 32 Stat Points in Attack.
Venusaur has 508 total Stat Points, which is more than this format's limit of 66.
```

Impact: `config.py` constants were wrong; `Team.validate()` would have accepted
teams Showdown rejects, which is precisely the silent-corruption failure the
validator exists to prevent. Corrected before any dependent code was written.

## Finding 2: Restricted item pool

148 legal items total, of which 75 are Mega Stones — so only **73 non-stone
items**. Several mainline staples are absent: Choice Band, Rocky Helmet,
Assault Vest, Sitrus Berry all fail validation with "does not exist in Gen 9".

Impact: `Team.validate()` must check items against the Champions item pool, not
assume mainline availability. Already in the design (`dex.items`), but the
fixture teams in the plan used illegal items and needed replacing.

## Finding 3: Mega designation is the classic `requiredItem` mechanic

98 Mega formes in the mod (75 with standard Mega Stones). Megas are designated
by holding the corresponding stone — no separate team-paste field.

```json
{
  "name": "Charizard-Mega-Y",
  "baseSpecies": "Charizard",
  "forme": "Mega-Y",
  "requiredItem": "Charizardite Y",
  "types": ["Fire", "Flying"],
  "isMega": true
}
```

Impact: simpler than feared. Item Clause already prevents duplicate stones, and
one-Mega-per-team is enforced by the sim. No special paste handling needed.

## Finding 4: `accept_open_team_sheet` must be set explicitly

poke-env's `Player.__init__` takes `accept_open_team_sheet: bool = False`. The
Reg M-B ruleset includes Open Team Sheets, but poke-env does **not** opt in by
default.

Observed in the spike (with the default `False`): `opponent_team` had only 4
entries — the mons actually brought — not the full 6 visible at preview.

Impact: the bring-4 policy depends on seeing the opponent's full team at
preview. `sim.py` must pass `accept_open_team_sheet=True`, or the policy will
select against partial information and every result will be subtly wrong.

## Finding 5: no GenData patch needed (plan Task 4 dropped)

The plan predicted poke-env would be missing Champions species, since
`GenData.from_format` does `int(format[3])` and so loads stock gen-9 data.
The first half is true; the conclusion was not.

Measured against poke-env 0.15.0:

```
poke-env gen: 9  pokedex size: 1599  moves: 954
champions species missing from poke-env: 0
champions moves missing from poke-env: 0
missing MEGAS: 0
species with MISMATCHED baseStats: 0
species with MISMATCHED types: 0
```

poke-env 0.15.0 bundles data generated from a Showdown master recent enough to
carry the Champions species, all 76 Megas, and their rebalanced stats. Task 4
(`patch_gen_data`) is deleted rather than written.

`pvgc/dex.py` is still required — it is the source of *legality* (which species,
moves, and items are standard in Reg M-B), which poke-env's dex does not encode.

## Species pool

- 1517 total species in the mod
- 281 non-Mega, non-Gmax, standard species — the Reg M-B roster

## poke-env API signatures (verified, not assumed)

```
Player.__init__(self, account_configuration=None, *, avatar=None,
    battle_format='gen9randombattle', log_level=None, max_concurrent_battles=1,
    accept_open_team_sheet=False, save_replays=False,
    server_configuration=..., start_timer_on_battle_start=False,
    start_listening=True, open_timeout=10.0, ping_interval=20.0,
    ping_timeout=20.0, loop=..., team=None, strict_battle_tracking=False)

Player.choose_move(self, battle: AbstractBattle) -> Union[BattleOrder, Awaitable[BattleOrder]]
Player.teampreview(self, battle: AbstractBattle) -> Union[str, Awaitable[str]]
Player.battle_against(self, *opponents: Player, n_battles: int = 1)
```

`teampreview` may return an awaitable — the bring policy override may be sync.

Battle attributes confirmed present: `battle.turn`, `battle.won`, `battle.team`,
`battle.opponent_team` (dict keyed by identifier, values have `.species`).

## Spike battle result

```
finished: 1 won: 1
battle-gen9championsvgc2026regmb-1 turns=12 won=True
  our team size: 6
  opponent team size: 4
  opponent species: ['charizard', 'blastoise', 'venusaur', 'beedrill']
```

A full Reg M-B doubles battle ran to completion and parsed. Outcome 1 of the
three the plan anticipated.

## Environment gotchas

- The sandbox blocks socket binding; the Showdown server needs the sandbox
  override to run (`bind EPERM 0.0.0.0:8000`).
- `SSLKEYLOGFILE=/tmp/sslkeylogfile.txt` in the shell profile breaks `pip` under
  the sandbox. Unset it per-command.
- `git config url."git@github.com:".insteadOf https://github.com/` rewrites the
  Showdown clone URL to SSH; override with
  `-c 'url.https://github.com/.insteadOf=git@github.com:'`.
- npm `allowScripts` policy blocks `esbuild`'s postinstall. Harmless — the
  Showdown build succeeds anyway.

## Plan corrections required

1. `config.py`: `MAX_EVS_TOTAL = 508` → `66`, `MAX_EVS_PER_STAT = 252` → `32`.
   Rename to `MAX_STAT_POINTS_TOTAL` / `MAX_STAT_POINTS_PER_STAT`.
2. Task 6 validator: check items against the Champions pool; use stat-point
   limits; error strings say "stat point", not "EV".
3. Task 6 and Task 10 test fixtures: replace illegal items and any species not
   in the 281-mon roster.
4. Task 12: pass `accept_open_team_sheet=True` to both players.
5. Task 12: `ShowdownServer` startup must tolerate the REPL `CRASH` lines the
   server emits before `Worker 1 now listening` — they are non-fatal.
