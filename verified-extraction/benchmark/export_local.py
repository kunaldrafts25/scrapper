"""Export a previously approved local job to a frozen, unlabeled bundle."""
import argparse
from pathlib import Path

from .frozen import export_local
from verified_extraction.store import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--site-id", required=True)
    parser.add_argument("--split", choices=["development", "held_out"], required=True)
    parser.add_argument("--category", required=True)
    parser.add_argument("--plan-name", required=True)
    parser.add_argument("--permission-note", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = export_local(Store(args.db), args.tenant, args.job_id, args.output,
                            args.site_id, args.split, args.category, args.plan_name, args.permission_note)
    print(f"Exported {manifest['site_id']} with {len(manifest['pages'])} page outcomes to {args.output}")


if __name__ == "__main__":
    main()
