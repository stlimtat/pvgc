"""M0 spike: can poke-env run a Reg M-B battle at all?

Start the Showdown server first:
  cd data/pokemon-showdown && node pokemon-showdown start --no-security
"""
import asyncio
import inspect
import sys
from pathlib import Path

from poke_env import AccountConfiguration, LocalhostServerConfiguration
from poke_env.player import Player, RandomPlayer
from poke_env.teambuilder import ConstantTeambuilder

from pvgc.config import FORMAT_ID, ROOT

TEAM = (ROOT / "tests" / "fixtures" / "spike_team.txt").read_text()


def report_api():
    """Record the exact signatures the rest of the plan codes against."""
    for name in ("__init__", "choose_move", "teampreview", "battle_against"):
        fn = getattr(Player, name, None)
        print(f"Player.{name}:", inspect.signature(fn) if fn else "MISSING")


async def main():
    report_api()
    players = [
        RandomPlayer(
            account_configuration=AccountConfiguration(f"pvgcspike{i}", None),
            server_configuration=LocalhostServerConfiguration,
            battle_format=FORMAT_ID,
            team=ConstantTeambuilder(TEAM),
            max_concurrent_battles=1,
        )
        for i in (1, 2)
    ]
    p1, p2 = players
    await p1.battle_against(p2, n_battles=1)
    print("finished:", p1.n_finished_battles, "won:", p1.n_won_battles)
    for tag, battle in p1.battles.items():
        print(f"{tag} turns={battle.turn} won={battle.won}")
        print("  our team size:", len(battle.team))
        print("  opponent team size:", len(battle.opponent_team))
        print("  opponent species:", [p.species for p in battle.opponent_team.values()])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
