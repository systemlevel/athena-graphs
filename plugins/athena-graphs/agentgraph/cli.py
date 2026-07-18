"""Command-line surface for Athena Graphs."""

from __future__ import annotations

import argparse
import json
import sys

from .adapters import BACKENDS
from .service import diagram_impl, doctor_impl, run_graph_impl


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentgraph", description="Run and inspect AI agent graphs")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run the prebuilt plan/work/review graph")
    run.add_argument("--goal", required=True)
    run.add_argument("--criteria", default="The requested goal is complete and correct.")
    run.add_argument("--backend", default="mock", choices=["auto", *BACKENDS])
    run.add_argument("--cwd")
    run.add_argument("--model")
    run.add_argument("--timeout", type=float)
    run.add_argument("--skip-permissions", action="store_true")
    run.add_argument("--max-revisions", type=int, default=2)
    run.add_argument("--workers", type=int, default=4)
    run.add_argument("--json", action="store_true")

    commands.add_parser("diagram", help="print the prebuilt graph as Mermaid")
    commands.add_parser("backends", help="list supported agent backends")
    doctor = commands.add_parser("doctor", help="check backend and directory availability")
    doctor.add_argument("--cwd")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "diagram":
            print(diagram_impl())
            return 0
        if args.command == "backends":
            print("\n".join(BACKENDS))
            return 0
        if args.command == "doctor":
            print(json.dumps(doctor_impl(args.cwd), indent=2))
            return 0
        result = run_graph_impl(
            args.goal,
            args.criteria,
            backend=args.backend,
            cwd=args.cwd,
            model=args.model,
            timeout=args.timeout,
            skip_permissions=args.skip_permissions,
            max_revisions=args.max_revisions,
            worker_count=args.workers,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(result["summary"])
            if result["state"].get("final_output"):
                print("\n" + result["state"]["final_output"])
        return 0 if result["approved"] else 1
    except Exception as exc:
        print(f"agentgraph: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
