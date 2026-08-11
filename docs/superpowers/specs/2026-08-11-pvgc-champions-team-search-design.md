# PVGC — Pokémon Champions Team Search

**Date:** 2026-08-11
**Status:** Approved, ready for implementation planning
**Scope:** Battle harness, gauntlet fitness function, and LLM-driven team search for Pokémon Champions VGC 2026 Regulation M-B.

## Goal

Find strong Reg M-B teams, and test strategic hypotheses about the metagame, by
simulating battles on a local Pokémon Showdown server. An LLM proposes candidate
teams grounded in current usage data; a fast non-LLM agent plays them against a
fixed pool of meta teams; results feed back into the next proposal.

## Target format

Verified against upstream sources on 2026-08-11:

| Property | Value |
|---|---|
| Showdown format id | `gen9championsvgc2026regmb` |
| Showdown mod | `champions` |
| Ruleset | `Flat Rules`, `VGC Timer`, `Open Team Sheets` |
| Structure | Double battles, bring 4 of 6, level 50 |
| Clauses | Item Clause, Species Clause |
| Mechanic | Mega Evolution — one per team per battle, post-Mega speed for turn order, persists through switches. No Terastal. |
| Active | 2026-06-17 – 2026-09-09 (2026 World Championships format) |
| Usage stats | `smogon.com/stats/2026-07/` → `gen9championsvgc2026regmb-1760.txt` and chaos JSON |

Sources: [Victory Road regulations](https://victoryroad.pro/champions-regulations/),
[Showdown `config/formats.ts`](https://github.com/smogon/pokemon-showdown/blob/master/config/formats.ts),
[Smogon stats archive](https://www.smogon.com/stats/2026-07/).

### Consequences of the format that drive the design

1. **Open Team Sheets.** Both players see full teams at preview. No hidden-set
   inference, no belief tracking. Agent state is simpler, and team-preview logic
   may legitimately use the opponent's full team.
2. **Bring 4 of 6.** A 6-mon team is a menu of 15 possible brings. A fitness
   function that ignores bring selection measures the wrong object.
3. **Mega Evolution.** An extra action dimension and a once-per-battle
   irreversible commitment.
4. **The format is new.** Reg M-B went live 2026-06-17, and 11 of its Megas exist
   in no mainline game. No LLM has memorized this metagame. All meta knowledge
   must be injected from fetched data, never recalled.
5. **Thin stats.** July 2026 is the first full month of data. The generator will
   oversample the top ~30 species. Improves monthly.

## Architecture

Three layers, one seam.

```
proposer  ->  score_team(team, gauntlet) -> GauntletResult  ->  engine
(LLM)              THE SEAM                                  (poke-env + PS)
```

`score.py` is the only interface between search and simulation. `GauntletResult`
is plain dataclasses with no poke-env types crossing the boundary. This is where
a faster engine, or a trained RL policy, replaces the current implementation
without touching anything above.

### Engine choice

**Chosen: poke-env against a local Showdown server (websocket).** poke-env
supplies doubles battle-state parsing, action-space handling, a heuristic
baseline player, and a `Player` interface already shaped for a later RL policy.
The battle-state parser is the expensive artifact and it already exists.

**Known upgrade path, not built:** `pokemon-showdown simulate-battle` over
stdin/stdout — no server, no websockets, deterministic seeding, trivially
parallel — at the cost of writing the state parser. Adopt if throughput binds or
if reproducible battles are needed for debugging. Contained by the seam.

**Rejected:** running both engines simultaneously (two engines, two sets of bugs,
they will disagree); reimplementing the battle engine in Python.

### Module layout

```
pvgc/
  config.py    format id, stats month, cutoff, paths — constants, no framework
  dex.py       Champions dex/moves from PS mod data; Reg M-B legality rules
  team.py      Team + Mon dataclasses, paste parse/serialize, validate()
  usage.py     fetch + cache Smogon chaos JSON; typed accessors
  teamgen.py   usage stats -> gauntlet teams via teammate-correlation walk
  sim.py       Showdown server lifecycle, poke-env players, bring policy
  score.py     score_team(team, gauntlet) -> GauntletResult   <-- THE SEAM
  store.py     sqlite3, four tables, plain SQL
  propose.py   LLM proposer loop
  cli.py       argparse subcommands
tests/
scripts/
  gen_poke_env_data.py   only if the spike shows poke-env lacks Champions data
data/                    gitignored: cached stats, sqlite db, PS checkout
```

`dex.py` splits from `team.py` because only validation needs the dex. `usage.py`
splits from `teamgen.py` because usage data has two consumers: the gauntlet
generator and the LLM proposer.

**Dependencies:** `poke-env`, one LLM SDK (`anthropic` and/or `google-genai`).
Node-side: a built `pokemon-showdown` checkout under `data/`.

**Deliberately absent:** no ORM (`sqlite3` is stdlib), no config framework, no
HTTP client dependency (`urllib.request` fetches one JSON file per month), no
player class hierarchy, no web UI, no plugin system, no abstract base classes
with a single implementation.

## Data flow

```
[0] usage.py    Smogon chaos JSON -> data/stats/2026-07/...-1760.json  (monthly, cached)
[1] teamgen.py  sample 16 gauntlet teams -> validate -> store, pin hash
[2] propose.py  LLM(usage priors + prior results) -> k candidate pastes -> validate
[3] score.py    candidate x gauntlet x N battles -> GauntletResult
[4] store.py    sqlite -> aggregate feeds back into [2]
```

### Gauntlet construction

Sample a lead by usage weight, then add five more by **teammate correlation**
from the chaos JSON's `Teammates` field rather than by naive top-6 usage. Sample
each mon's item, ability, moves, and EV spread from its own weighted
distributions (`Items`, `Abilities`, `Moves`, `Spreads`). Enforce Item Clause
during sampling.

Use the `-1760` rating cutoff. Lower buckets model ladder noise, not the meta.

These are statistical composites, not real tournament teams — but teammate
correlation carries genuine synergy signal, and the pool self-refreshes monthly.

### Storage

| table | columns |
|---|---|
| `team` | `id, hash, paste, role('gauntlet'\|'candidate'), source, created_at, meta_json` |
| `run` | `id, started_at, format_id, stats_month, cutoff, gauntlet_hash, n_battles, notes` |
| `matchup` | `id, run_id, candidate_id, opponent_id, n, wins, losses` |
| `battle` | `id, matchup_id, seed, winner, turns, bring_a_json, bring_b_json` |

`team.hash` is canonical identity (sorted species + item + moves + spread) so a
re-proposed team dedupes instead of re-scoring.

`run.gauntlet_hash` pins the opponent pool. **Scores are comparable only within a
run.** Enforced in code, not left to documentation.

`battle` rows are retained because bring-4 distributions live in them, and "which
four does this team actually lead, per matchup" is a primary output. Battle
*logs* are written only under a debug flag.

## Two correctness hazards

These are the places where the system can quietly produce confident, wrong
numbers. Both are handled structurally.

### 1. Bring-4 selection is a required policy

`SimpleHeuristicsPlayer` has no meaningful team-preview logic. If both sides
bring the first four, the 6-mon design space collapses to 4 and every result is
meaningless.

v1 bring policy: score all 15 combinations by summed type matchup and speed tier
against the revealed opponent team (legitimate under Open Team Sheets), take the
argmax. Crude, but explicit, and applied symmetrically to both sides.

### 2. Variance will fool you

At N=50 per matchup, per-matchup win rate carries roughly ±7% standard error.
Aggregated over 16 matchups (800 battles) that falls to about ±1.8%.

Therefore: **overall scores are meaningful; individual matchup cells are noisy.**
Two candidates 3% apart overall are indistinguishable. All reported figures carry
confidence intervals — a bare percentage handed to an LLM will be narrativized
with unearned confidence.

Upgrade path, not built: adaptive allocation (more battles to matchups near 50%)
and paired seeds (candidates face identical RNG), which sharply cuts variance for
comparisons. Add when scores stop separating.

## LLM proposer

**Grounded, never recalled.** The model does not know Reg M-B. Every prompt
carries the facts; the model contributes reasoning over them.

| Injected | Source |
|---|---|
| Top ~40 species by usage with usage %, top items/abilities/moves/spreads, top teammates | `usage.py` |
| Mega-capable species with post-Mega stats, types, abilities | `dex.py` |
| Reg M-B rules: item clause, species clause, 4-of-6, one Mega, no Tera | `config.py` |
| Prior results: top teams by score with per-matchup CIs and bring distributions | `store.py` |

If a fact is not in these tables, it does not enter the prompt.

**Output contract:** JSON — `{paste, hypothesis, changed_from}`. `hypothesis` is a
falsifiable claim, stored on `team.meta_json`, so that when the team is scored the
claim sits beside its verdict. This is the mechanism for testing strategic ideas
against the metagame.

**Validation is a hard gate.** Every paste passes `team.validate()`. On failure,
one repair attempt with the specific error text, then discard and log. No
unbounded retries. The failure log is signal about what the model gets wrong.

**Proposal modes** (one prompt, mode as parameter):
- `seed` — cold start, k diverse teams from usage priors
- `mutate` — take a high scorer, change 1–2 slots
- `probe` — test a stated hypothesis

**Overfitting mitigation.** A search loop aimed at 16 fixed teams will find teams
that beat *those 16*. Split the gauntlet **12 train / 4 holdout**. The proposer
sees train results only. Every candidate is scored on both, and both are
reported. Train ≫ holdout means the loop is fitting the pool — visible as a
number rather than discovered at a tournament.

Cost is negligible: scoring throughput caps the loop at roughly 15–30 candidates
per hour, so LLM calls are a rounding error. Spend tokens on grounding.

**Deliberately absent:** no agent framework, no tool-calling loop, no multi-agent
debate, no vector store.

## Failure handling

The governing rule: **a battle that did not happen must never count as a battle.**

| Failure | Response |
|---|---|
| Showdown crash or battle hang | timeout, retry once, else mark `failed` and exclude from N |
| poke-env parse error (unknown species, mega desync) | fail loud, abort matchup, log raw protocol |
| Smogon fetch fails | fall back to cache *and record that in the run*; no cache → hard error |
| LLM invalid JSON or illegal team | one repair attempt, then discard and log |
| Showdown rejects a team at battle start | hard error — the validator has a gap, which is a bug |

`matchup.n` is derived from recorded battles, never from the requested count, with
an assertion that `n == wins + losses`. Win rates are reported alongside
`failed_count`. Silently counting a crashed battle as a loss corrupts every
downstream number and is invisible; that is the failure mode this rule exists to
prevent.

## Testing

pytest, no additional framework.

- `team.py` — paste → Team → paste roundtrip; one test per validator rejection
  rule (duplicate item, duplicate species, EV overflow, illegal move, two Megas)
- `usage.py` — parse a checked-in trimmed chaos JSON fixture (~50KB)
- `teamgen.py` — same seed produces same teams; every generated team passes
  `validate()`
- `score.py` — aggregation math against hand-computed numbers: confidence
  intervals and failed-battle exclusion, no simulator required
- One integration test, marked slow — real local server, heuristic vs heuristic,
  one Reg M-B battle completes and parses

## Milestones

| | Deliverable | Value |
|---|---|---|
| **M0** | Spike: does poke-env work against `gen9championsvgc2026regmb`? | de-risks everything (1 day) |
| **M1** | Local PS server + one heuristic-vs-heuristic battle parsed | harness lives |
| **M2** | `team.py` + validator + tests | nothing illegal reaches the sim |
| **M3** | `usage.py` + `teamgen.py` → 16 valid gauntlet teams | meta pool exists |
| **M4** | `score.py` + `store.py` → paste a team, get a matchup table with CIs and bring distributions | useful standalone |
| **M5** | `propose.py` → the LLM loop | the original goal |

### M0 is a gate, not a formality

poke-env generates its `GenData` from Showdown's dex files, and Champions species
and Megas live in `data/mods/champions/`. Three possible outcomes, timeboxed to
one day:

- Works as-is → proceed
- Needs a data-generation script (`scripts/gen_poke_env_data.py`) → +2–3 days,
  same architecture
- poke-env's parser cannot handle Mega mechanics in doubles → reopen the engine
  decision in favour of `simulate-battle`

### M4 is the target to aim at

At M4 the project is a tool a VGC player would use unprompted: hand it a team, it
reports what the team loses to and what it brings. It stands alone if the LLM
loop disappoints, and its output is the evidence for whether the heuristic agent
is strong enough to trust.

## Out of scope

- **RL battle agent and remote training.** Separate subsystem, separate spec.
  Slots in behind `score.py` once M4 shows the fitness function is the weak link.
  The local machine is an Intel Mac (no CUDA, no MPS), so training belongs on a
  separate box regardless; local work is CPU- and Node-bound by the simulator.
- Ladder play against human opponents.
- Any web interface or dashboard.

## Known limitations

- Gauntlet teams are statistical composites, not real tournament teams.
- Fitness is measured under a heuristic policy; "best team" means "best when
  played by this agent against this pool".
- One month of usage data; the tail is thin.
- Reg M-B ends 2026-09-09. Retargeting is a config change plus a fresh stats
  month.
