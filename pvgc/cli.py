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
    CANDIDATES_PER_ROUND,
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


def cmd_propose(args) -> int:
    """Propose -> score -> feed back. The M5 loop."""
    from pvgc.propose import propose

    gauntlet, from_cache = _load_gauntlet(args.size)
    holdout = _holdout_ids(args.size)
    usage, _ = fetch()

    store = Store()
    gauntlet_hash = "".join(t.hash()[:4] for t in gauntlet)
    run_id = store.start_run(
        gauntlet_hash=gauntlet_hash, n_battles=args.n, from_cache=from_cache,
        notes=f"propose x{args.rounds}",
    )
    # Register the gauntlet up front so holdout opponents can be excluded from
    # the proposer's feedback by team id.
    opponent_ids = [
        store.add_team(t, role="gauntlet", source="usage") for t in gauntlet
    ]
    holdout_team_ids = {opponent_ids[i] for i in holdout}

    total = args.rounds * args.k * args.size * args.n
    print(f"run {run_id}: {args.rounds} rounds x {args.k} candidates, "
          f"up to {total} battles", file=sys.stderr)

    with ShowdownServer(port=args.port):
        for rnd in range(args.rounds):
            # Train split only. Holdout opponents are excluded outright so
            # the overfit gap stays an honest out-of-sample measure.
            prior = store.scored_candidates(
                run_id, holdout_team_ids=holdout_team_ids
            )
            mode = args.mode if rnd == 0 else "mutate"
            print(f"\n=== round {rnd + 1}/{args.rounds} ({mode}) ===",
                  file=sys.stderr)

            accepted, rejected = propose(usage, prior, mode=mode, k=args.k)
            for _, errors in rejected:
                print(f"  discarded: {errors[0]}", file=sys.stderr)

            for team, proposal in accepted:
                candidate_id = store.add_team(
                    team, role="candidate", source="llm",
                    meta={"hypothesis": proposal.hypothesis,
                          "changed_from": proposal.changed_from,
                          "round": rnd},
                )
                result = asyncio.run(
                    score_team(team, gauntlet, holdout, n=args.n)
                )
                for m in result.matchups:
                    opponent_id = store.add_team(
                        gauntlet[m.opponent_id], role="gauntlet", source="usage"
                    )
                    matchup_id = store.add_matchup(
                        run_id, candidate_id, opponent_id, n=m.n,
                        wins=m.wins, losses=m.losses, failed=m.failed,
                    )
                    for winner, turns, bring_a, bring_b in m.battles:
                        store.add_battle(
                            matchup_id, seed=None, winner=winner, turns=turns,
                            bring_a=list(bring_a or []),
                            bring_b=list(bring_b or []),
                        )
                lo, hi = result.overall_interval
                # flush: this runs for minutes, and progress on stderr would
                # otherwise arrive out of order with buffered stdout.
                print(f"  {result.candidate_hash}  "
                      f"{result.overall_winrate:6.1%} [{lo:.1%}, {hi:.1%}]  "
                      f"gap {result.overfit_gap:+.1%}  "
                      f"| {proposal.hypothesis[:60]}", flush=True)

    print(f"\n=== run {run_id} leaderboard ===")
    print(f"{'team':>17}  {'overall':>8}  {'95% CI':>18}  {'n':>5}  hypothesis")
    for r in store.scored_candidates(run_id):
        print(f"{r['hash']:>17}  {r['overall']:7.1%}  "
              f"[{r['lo']:6.1%},{r['hi']:6.1%}]  {r['n']:5d}  "
              f"{r['hypothesis'][:60]}")
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

    p_prop = sub.add_parser("propose", help="run the LLM search loop")
    p_prop.add_argument("--rounds", type=int, default=3)
    p_prop.add_argument("-k", type=int, default=CANDIDATES_PER_ROUND,
                        help=f"candidates per round (default {CANDIDATES_PER_ROUND})")
    p_prop.add_argument("-n", type=int, default=BATTLES_PER_MATCHUP)
    p_prop.add_argument("--mode", choices=["seed", "probe"], default="seed")
    p_prop.add_argument("--port", type=int, default=8000)

    args = parser.parse_args()
    return {
        "gauntlet": cmd_gauntlet,
        "score": cmd_score,
        "propose": cmd_propose,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
