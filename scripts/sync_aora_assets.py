"""Copy the canonical desktop Aora bundle into Android's offline assets."""
import argparse
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
source = root / "desktop/src/vendor/aora-bot"
target = root / "android-app/app/src/main/assets/aora-bot"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
target.mkdir(parents=True, exist_ok=True)
for item in sorted(source.iterdir()):
    if not item.is_file():
        continue
    destination = target / item.name
    if args.check:
        if not destination.exists() or destination.read_bytes() != item.read_bytes():
            raise SystemExit(f"Out of sync: {destination}; run python3 scripts/sync_aora_assets.py")
    else:
        shutil.copyfile(item, destination)
print("Aora assets are synchronized.")
