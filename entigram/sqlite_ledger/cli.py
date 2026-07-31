import sys
import argparse
import json
from .manager import LedgerManager

def main():
    parser = argparse.ArgumentParser(description="Entigram Decision Ledger CLI")
    parser.add_argument("--db", default=".etg/state.db", help="Path to the SQLite ledger DB")
    
    subparsers = parser.add_subparsers(dest="command")
    
    # List conflicts
    subparsers.add_parser("list-conflicts", help="List all pending conflicts")
    
    # List resolutions
    subparsers.add_parser("list-resolutions", help="List all resolutions")
    
    # Resolve conflict
    resolve_parser = subparsers.add_parser("resolve", help="Resolve a conflict")
    resolve_parser.add_argument("conflict_id", help="ID of the conflict to resolve")
    resolve_parser.add_argument("--state", required=True, help="The resolved state (string or JSON)")
    resolve_parser.add_argument("--rationale", required=True, help="Rationale for the decision")
    resolve_parser.add_argument("--type", default="generic", help="Entity type")

    args = parser.parse_args()
    
    manager = LedgerManager(args.db)
    
    if args.command == "list-conflicts":
        conflicts = manager.get_pending_conflicts()
        if not conflicts:
            print("No pending conflicts.")
        for c in conflicts:
            print(f"ID: {c['conflict_id']} | Type: {c['entity_type']} | Time: {c['timestamp']}")
            print(f"Proposed: {c['proposed_states']}")
            print("-" * 20)
            
    elif args.command == "list-resolutions":
        res = manager.get_all_resolutions()
        if not res:
            print("No resolutions recorded.")
        for r in res:
            print(f"ID: {r['conflict_id']} | Type: {r['entity_type']} | Time: {r['timestamp']}")
            print(f"Decision: {r['state']}")
            print(f"Rationale: {r['rationale']}")
            print("-" * 20)
            
    elif args.command == "resolve":
        if manager.record_resolution(args.conflict_id, args.type, args.state, args.rationale):
            print(f"Successfully resolved {args.conflict_id}")
        else:
            print(f"Failed to resolve {args.conflict_id}")
            sys.exit(1)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
