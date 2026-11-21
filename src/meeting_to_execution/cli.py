from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from .config import get_settings
from .container import Container
from .demo import run_demo
from .evals import DEFAULT_FIXTURES, DEFAULT_GOLDEN, run_evals
from .schemas import (
    DecisionReviewInput,
    MeetingCreate,
    ReviewSubmission,
    TaskReviewInput,
)


def _init() -> None:
    settings = get_settings()
    container = Container.build(settings)
    container.close()
    print(f"Initialized database at {settings.sqlite_path}")


def _serve(host: str, port: int, reload: bool) -> None:
    uvicorn.run(
        "meeting_to_execution.api:app",
        host=host,
        port=port,
        reload=reload,
    )


def _seed() -> None:
    settings = get_settings()
    container = Container.build(settings)
    try:
        paths = sorted(Path("fixtures/meetings").glob("*.json"))
        if not paths:
            raise SystemExit("No fixture transcripts found")
        for path in paths:
            meeting = MeetingCreate.model_validate(json.loads(path.read_text()))
            if container.memory.get_meeting(meeting.meeting_id or ""):
                print(f"skip existing meeting {meeting.meeting_id}")
                continue
            thread_id = f"seed-{meeting.meeting_id}"
            container.pipeline.start(meeting, thread_id)
            packet = container.pipeline.get_packet(thread_id)
            if packet is None:
                print(f"no review packet for {meeting.title}")
                continue
            review = ReviewSubmission(
                packet_hash=packet.packet_hash,
                status="approved",
                decisions=[
                    DecisionReviewInput(
                        item_id=entry.item.id,
                        action="approve",
                        conflict_action=entry.proposed_conflict_action,
                    )
                    for entry in packet.decisions
                ],
                tasks=[
                    TaskReviewInput(task_id=task.id, action="approve")
                    for task in packet.tasks
                ],
                reviewer="seed",
            )
            result = container.pipeline.resume(thread_id, review)
            print(
                f"{meeting.title}: {len(result.get('approved_decisions', []))} decisions, "
                f"{len(result.get('posted', []))} posted"
            )
        print(json.dumps(container.memory.stats().model_dump(), indent=2))
    finally:
        container.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="mte", description="Meeting to Execution")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Create the SQLite database")
    serve = subparsers.add_parser("serve", help="Run the FastAPI server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    subparsers.add_parser("demo", help="Run the four-meeting end-to-end demo")
    subparsers.add_parser("seed", help="Ingest and approve the fixture meetings")
    evals = subparsers.add_parser("eval", help="Run the golden-set evaluation")
    evals.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    evals.add_argument("--golden", default=str(DEFAULT_GOLDEN))

    args = parser.parse_args()
    if args.command == "init":
        _init()
    elif args.command == "serve":
        _serve(args.host, args.port, args.reload)
    elif args.command == "demo":
        run_demo()
    elif args.command == "seed":
        _seed()
    elif args.command == "eval":
        report = run_evals(args.fixtures, args.golden)
        print(json.dumps(report.model_dump(), indent=2))
        raise SystemExit(0 if report.passed else 1)
