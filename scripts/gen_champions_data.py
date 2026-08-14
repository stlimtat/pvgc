"""Dump the resolved champions-mod dex to data/champions_dex.json."""
import json
import shutil
import subprocess
import sys

from pvgc.config import CHAMPIONS_DEX, ROOT, SHOWDOWN_DIR

DUMPER = "dump_champions_dex.js"


def main():
    if not SHOWDOWN_DIR.exists():
        sys.exit(f"No Showdown checkout at {SHOWDOWN_DIR}. See docs/superpowers/SPIKE-M0.md.")
    shutil.copy(ROOT / "scripts" / DUMPER, SHOWDOWN_DIR / DUMPER)
    proc = subprocess.run(
        ["node", DUMPER], cwd=SHOWDOWN_DIR, capture_output=True, text=True
    )
    (SHOWDOWN_DIR / DUMPER).unlink(missing_ok=True)
    if proc.returncode != 0:
        sys.exit(f"node failed:\n{proc.stderr}")
    data = json.loads(proc.stdout)
    CHAMPIONS_DEX.parent.mkdir(parents=True, exist_ok=True)
    CHAMPIONS_DEX.write_text(json.dumps(data))
    megas = sum(1 for s in data["species"].values() if s["isMega"])
    stones = sum(1 for i in data["items"].values() if i["megaStone"])
    print(
        f"species={len(data['species'])} (megas={megas}) "
        f"moves={len(data['moves'])} learnsets={len(data['learnsets'])} "
        f"items={len(data['items'])} (stones={stones}) "
        f"abilities={len(data['abilities'])} natures={len(data['natures'])}"
    )
    if megas == 0:
        sys.exit("ERROR: zero megas extracted — isMega detection is wrong")


if __name__ == "__main__":
    main()
