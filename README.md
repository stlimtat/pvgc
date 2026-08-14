# pvgc

Team search for Pokémon Champions VGC 2026 Regulation M-B
(`gen9championsvgc2026regmb`), driven by local Pokémon Showdown simulation.

Hand it a team paste; it tells you what the team loses to and what it brings.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

git clone --depth 1 https://github.com/smogon/pokemon-showdown.git data/pokemon-showdown
cd data/pokemon-showdown && npm install && npm run build && cd -

.venv/bin/python scripts/gen_champions_data.py
```

The last step extracts the Reg M-B dex from the Showdown checkout. Re-run it
whenever you update the checkout.

## Use

```bash
.venv/bin/pvgc gauntlet                    # build and show the opponent pool
.venv/bin/pvgc score team.txt              # score a team paste
.venv/bin/pvgc score team.txt -n 20        # 20 battles per matchup
.venv/bin/pvgc --size 24 score team.txt    # larger gauntlet
```

`score` starts and stops its own Showdown server. If you already have one
running, pass `--no-server`.

## Reading the numbers

**Individual matchup cells are noisy; the overall figure is not.** At the
default 50 battles per matchup a single cell carries roughly ±7% standard
error, while the pooled overall figure is nearer ±1.8%. Two teams within about
3% overall are indistinguishable. Every rate is printed with a 95% Wilson
interval — read the interval, not the point estimate.

**The overfit gap is the number that keeps you honest.** The gauntlet splits
into train and holdout; a large positive `train - holdout` gap means results
are fitting the specific opponent pool rather than measuring team quality.

**Failed battles are excluded, never counted as losses.** If any occur, the
output says so explicitly. `n` is always derived from battles that actually
finished.

**Fitness is measured under a heuristic policy.** "Best team" here means "best
when played by this agent against this pool", not "best in a strong human's
hands". See Limitations.

## What the format is

Verified against Showdown's resolved rule table, not assumed:

| | |
|---|---|
| Structure | Double battles, bring 4 of 6, auto-level 50 |
| Stat Points | **66 total, 32 per stat** — Champions does not use mainline EVs |
| Clauses | Item, Species, Nickname |
| Mega Evolution | Via Mega Stone (`requiredItem`); one Mega per battle |
| Team sheets | Open Team Sheets — both players see full teams at preview |
| Roster | 281 non-Mega species, 76 Mega formes, 73 non-stone items |

Details and the M0 verification in `docs/superpowers/SPIKE-M0.md`.

## How it works

```
usage.py    Smogon chaos JSON (2026-07, 1760 cutoff)
teamgen.py  lead by usage, then fill by teammate correlation -> gauntlet
score.py    candidate x gauntlet x N battles          <-- THE SEAM
sim.py      local Showdown + poke-env heuristic player
store.py    sqlite
```

`score.py` is the only interface between search and simulation, and nothing
from poke-env crosses it. Replacing the engine or the battle policy is a change
below that line only.

## Limitations

- **Gauntlet teams are statistical composites**, built from usage distributions
  and teammate correlation. They are not real tournament teams.
- **The bring-4 policy is crude** — type matchup plus speed tier over all 15
  combinations (`pvgc/bring.py`). It is deterministic, so the bring
  distribution shows one entry per matchup; it becomes informative only with a
  stochastic or learned policy.
- **The battle policy is `SimpleHeuristicsPlayer`** with two local fixes: an
  explicit bring policy (the base class picks *randomly*) and Mega Evolution
  (the base class never Megas at all). It is still far from strong play.
- **One month of usage data.** Reg M-B began 2026-06-17, so the tail is thin
  and the top ~30 species are oversampled.
- **Reg M-B ends 2026-09-09.** Retargeting is a change to `pvgc/config.py` plus
  a fresh stats month.

## Testing

```bash
.venv/bin/pytest              # fast tests
.venv/bin/pytest -m slow      # requires the Showdown checkout; runs real battles
```

The slow tests include an oracle cross-check: generated teams are validated by
Showdown's own `TeamValidator`. Our validator agreeing with itself proves
nothing — the simulator uses Showdown's, and a team we accept that Showdown
rejects is a silently corrupted run.

## Notes

- `smogon.com` must be reachable to refresh usage statistics; cached data under
  `data/stats/` is reused otherwise, and every run records which it used.
- The Showdown server binds a socket and writes logs, so it needs to run
  outside a restrictive sandbox.
