"""Score independently labeled frozen captures without any network access."""
import argparse
import json
from pathlib import Path

from .frozen import load_labeled_case
from .run import EXTERNAL, score


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundles", type=Path, required=True)
    parser.add_argument("--split", choices=["development", "held_out", "all"], default="all")
    parser.add_argument("--adapter", choices=["local", *EXTERNAL], default="local")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.adapter != "local":
        raise SystemExit(f"{args.adapter} adapter is unconfigured; no result is claimed")
    paths = sorted(args.bundles.glob("*/manifest.json"))
    if not paths:
        raise SystemExit("No frozen site bundles found")
    cases = [load_labeled_case(path, path.with_name("labels.json")) for path in paths]
    sites = {}
    for case in cases:
        if case["site"] in sites:
            raise SystemExit("Duplicate site in frozen bundles")
        sites[case["site"]] = case["split"]
    selected = [case for case in cases if args.split == "all" or case["split"] == args.split]
    output = {"evaluation_schema_version": "1.0", "adapter": "local", "split": args.split,
              "offline_replay": True, "sites": len(selected), **score(selected)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(selected)} site results to {args.output}")


if __name__ == "__main__":
    main()
