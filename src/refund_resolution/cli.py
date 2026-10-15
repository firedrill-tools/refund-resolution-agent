import argparse
import json
import os
from pathlib import Path
from .service import resolve
from .workspace import Workspace


def main():
    parser = argparse.ArgumentParser(description="Resolve a synthetic refund ticket")
    parser.add_argument("ticket_id")
    parser.add_argument("--approval-id")
    parser.add_argument("--workspace", type=Path, default=Path("workspace.json"))
    parser.add_argument("--model", default=os.getenv("ANTHROPIC_MODEL", "anthropic:claude-sonnet-4-6"))
    args = parser.parse_args()
    if not args.workspace.exists():
        parser.error("workspace missing; copy fixtures/starter.json first")
    try:
        result = resolve(args.ticket_id, args.approval_id, Workspace(args.workspace), args.model)
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
