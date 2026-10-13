"""Command line entry point:  .venv/bin/python -m backend.run --ticket 101

Not used in Problem 5 on the real tickets; tickets are resolved in a later problem.
"""

import argparse
import asyncio

from .orchestrator import run_ticket


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Campus Customs agents on one ticket.")
    parser.add_argument("--ticket", type=int, required=True)
    args = parser.parse_args()
    outcome = asyncio.run(run_ticket(args.ticket))
    print(outcome.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
