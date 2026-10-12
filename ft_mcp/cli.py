"""`ft-migration list|apply|reject` for proposals the bot will approve."""

from __future__ import annotations

import argparse
import sys

from ft_mcp.proposals import MigrationError, apply_migration, list_migrations, reject_migration


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ft-migration")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show migration files and their status")

    apply_parser = sub.add_parser("apply", help="snapshot, apply a pending migration, regenerate SCHEMA.md")
    apply_parser.add_argument("id", help="version, version_name, or unique name")

    reject_parser = sub.add_parser("reject", help="mark a pending migration rejected")
    reject_parser.add_argument("id", help="version, version_name, or unique name")

    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            _print_list()
            return
        if args.command == "apply":
            result = apply_migration(args.id)
            print(f"applied {result['id']} {result['name']}")
            print(f"backup: {result['backup']}")
            print(f"git: {result['git']}")
            if result.get("warning"):
                print(result["warning"], file=sys.stderr)
            return
        result = reject_migration(args.id)
        print(f"rejected {result['id']} {result['name']}")
    except MigrationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


def _print_list() -> None:
    rows = list_migrations()
    if not rows:
        print("no migration files")
        return
    for row in rows:
        parts = [row["id"], row["name"], row["status"]]
        if row["classification"]:
            parts.append(row["classification"])
        if row["reason"]:
            parts.append(row["reason"])
        print("\t".join(parts))
