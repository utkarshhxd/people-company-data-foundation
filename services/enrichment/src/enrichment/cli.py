import argparse
import json
import sys

from common.logging import configure

from enrichment.pipeline import ProposalNotPending, accept, reject, run
from enrichment.repository import list_proposals


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="enrich",
        description="Ask a local model to propose golden-record gaps no vendor reported.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    do_run = sub.add_parser(
        "run", help="one bounded pass filing proposals for entities missing an eligible field"
    )
    do_run.add_argument("--entity-type", choices=["person", "company"], required=True)
    do_run.add_argument(
        "--limit-per-field", type=int, default=25,
        help="max entities to ask about, per eligible field, this run",
    )

    proposals = sub.add_parser(
        "proposals", help="the queue of AI guesses waiting on a human decision"
    )
    proposals_sub = proposals.add_subparsers(dest="proposals_command", required=True)

    list_cmd = proposals_sub.add_parser("list", help="list proposals")
    list_cmd.add_argument("--status", choices=["pending", "accepted", "rejected"])
    list_cmd.add_argument("--entity-type", choices=["person", "company"])
    list_cmd.add_argument("--json", action="store_true")

    accept_cmd = proposals_sub.add_parser(
        "accept", help="confirm a proposal -- writes it as a real observation"
    )
    accept_cmd.add_argument("--proposal-id", required=True)
    accept_cmd.add_argument("--reviewed-by", required=True)

    reject_cmd = proposals_sub.add_parser("reject", help="decline a proposal")
    reject_cmd.add_argument("--proposal-id", required=True)
    reject_cmd.add_argument("--reviewed-by", required=True)

    return parser


def cmd_run(args) -> int:
    result = run(args.entity_type, limit_per_field=args.limit_per_field)
    print(
        f"{result.entity_type}: {result.attempted} attempted, "
        f"{result.proposed} proposed, {result.declined} declined, "
        f"{result.errors} error(s)"
    )
    return 0


def cmd_proposals_list(args) -> int:
    from common.db import connect

    with connect() as conn:
        rows = list_proposals(conn, status=args.status, entity_type=args.entity_type)
    if args.json:
        print(json.dumps(
            [{**r, "proposal_id": str(r["proposal_id"]), "entity_id": str(r["entity_id"]),
              "stated_confidence": float(r["stated_confidence"])}
             for r in rows], indent=2, default=str,
        ))
        return 0
    if not rows:
        print("no proposals match")
        return 0
    for row in rows:
        print(
            f"{row['proposal_id']}  {row['entity_type']}/{row['entity_id']}  "
            f"{row['canonical_field']} -> {row['proposed_value']!r}  "
            f"[{row['model']} {row['stated_confidence']} {row['status']}]"
        )
    return 0


def cmd_proposals_accept(args) -> int:
    try:
        record_id = accept(args.proposal_id, args.reviewed_by)
    except ProposalNotPending as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{args.proposal_id} accepted -> observation {record_id}")
    print("run `golden build --entity-id <id>` to fold it into the golden record")
    return 0


def cmd_proposals_reject(args) -> int:
    if not reject(args.proposal_id, args.reviewed_by):
        print(f"error: no pending proposal {args.proposal_id}", file=sys.stderr)
        return 2
    print(f"{args.proposal_id} rejected")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure("enrichment")

    if args.command == "run":
        return cmd_run(args)

    return {
        "list": cmd_proposals_list,
        "accept": cmd_proposals_accept,
        "reject": cmd_proposals_reject,
    }[args.proposals_command](args)


if __name__ == "__main__":
    sys.exit(main())
