"""Command line interface.

  pvgc gauntlet          build and store the gauntlet pool
  pvgc score TEAM.txt    score a team paste against the gauntlet
"""
import argparse
import asyncio
import sys
from pathlib import Path

from pvgc.config import (
    BATTLES_PER_MATCHUP,
    GAUNTLET_SIZE,
    HOLDOUT_SIZE,
)
from pvgc.score import score_team
from pvgc.sim import ShowdownServer
from pvgc.store import Store
from pvgc.team import Team
from pvgc.teamgen import generate_gauntlet
from pvgc.usage import fetch


def _load_gauntlet(size: int) -> tuple[list[Team], bool]:
    usage, from_cache = fetch()
    return generate_gauntlet(usage, n=size, seed=0), from_cache


def _holdout_ids(size: int) -> set[int]:
    """The last HOLDOUT_SIZE opponents. The proposer never sees these.

    A gauntlet that is all holdout leaves the train split empty, which reports
    a meaningless 0.0% train rate and a nonsense overfit gap rather than
    failing — so refuse it outright.
    """
    if size <= HOLDOUT_SIZE:
        raise SystemExit(
            f"gauntlet size {size} must exceed holdout size {HOLDOUT_SIZE}; "
            f"use --size {HOLDOUT_SIZE + 1} or more"
        )
    return set(range(size - HOLDOUT_SIZE, size))


def _label(team: Team, width: int = 3) -> str:
    return ", ".join(m.species for m in team.mons[:width])


def cmd_gauntlet(args) -> int:
    teams, from_cache = _load_gauntlet(args.size)
    holdout = _holdout_ids(args.size)
    store = Store()
    for i, t in enumerate(teams):
        store.add_team(t, role="gauntlet", source="usage")
        tag = "holdout" if i in holdout else "train  "
        print(f"{i:2d}  {tag}  {t.hash()}  {', '.join(m.species for m in t.mons)}")
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

    gauntlet, from_cache = _load_gauntlet(args.size)
    holdout = _holdout_ids(args.size)

    store = Store()
    candidate_id = store.add_team(team, role="candidate", source="manual")
    gauntlet_hash = "".join(t.hash()[:4] for t in gauntlet)
    run_id = store.start_run(
        gauntlet_hash=gauntlet_hash, n_battles=args.n, from_cache=from_cache
    )

    total = args.size * args.n
    print(f"Running {total} battles ({args.size} opponents x {args.n})...",
          file=sys.stderr)

    if args.no_server:
        result = asyncio.run(score_team(team, gauntlet, holdout, n=args.n))
    else:
        with ShowdownServer(port=args.port):
            result = asyncio.run(score_team(team, gauntlet, holdout, n=args.n))

    for m in result.matchups:
        opponent_id = store.add_team(
            gauntlet[m.opponent_id], role="gauntlet", source="usage"
        )
        matchup_id = store.add_matchup(
            run_id, candidate_id, opponent_id,
            n=m.n, wins=m.wins, losses=m.losses, failed=m.failed,
        )
        for winner, turns, bring_a, bring_b in m.battles:
            store.add_battle(matchup_id, seed=None, winner=winner, turns=turns,
                             bring_a=list(bring_a or []), bring_b=list(bring_b or []))

    print(f"\nCandidate {result.candidate_hash}   run {run_id}\n")
    print(f"{'opp':>3}  {'winrate':>8}  {'95% CI':>18}  {'n':>4}  set      opponent")
    for m in result.matchups:
        lo, hi = m.interval
        tag = "holdout" if m.opponent_id in holdout else "train  "
        print(f"{m.opponent_id:3d}  {m.winrate:7.1%}  [{lo:6.1%},{hi:6.1%}]  "
              f"{m.n:4d}  {tag}  {_label(gauntlet[m.opponent_id])}...")

    lo, hi = result.overall_interval
    print(f"\noverall  {result.overall_winrate:6.1%}  [{lo:.1%}, {hi:.1%}]")
    print(f"train    {result.train_winrate:6.1%}")
    print(f"holdout  {result.holdout_winrate:6.1%}")
    print(f"overfit gap {result.overfit_gap:+.1%}"
          "   (large positive = fitting the gauntlet, not finding a good team)")
    if result.total_failed:
        print(f"\nWARNING: {result.total_failed} battles failed and were "
              "excluded from all denominators")

    print("\nbring distribution (most common per matchup):")
    for m in result.matchups:
        top = list(m.bring_distribution().items())[:2]
        pretty = "  |  ".join(
            f"{'/'.join(team.mons[i].species for i in combo)} x{count}"
            for combo, count in top
        )
        print(f"  {m.opponent_id:3d}  {pretty}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="pvgc")
    parser.add_argument("--size", type=int, default=GAUNTLET_SIZE,
                        help=f"gauntlet size (default {GAUNTLET_SIZE})")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("gauntlet", help="build and store the gauntlet pool")

    p_score = sub.add_parser("score", help="score a team paste")
    p_score.add_argument("paste", help="path to a Showdown team paste file")
    p_score.add_argument("-n", type=int, default=BATTLES_PER_MATCHUP,
                         help=f"battles per matchup (default {BATTLES_PER_MATCHUP})")
    p_score.add_argument("--port", type=int, default=8000)
    p_score.add_argument("--no-server", action="store_true",
                         help="assume a Showdown server is already running")

    args = parser.parse_args()
    return {"gauntlet": cmd_gauntlet, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
