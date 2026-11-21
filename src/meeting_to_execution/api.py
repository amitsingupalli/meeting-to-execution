from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from .config import Settings, get_settings
from .container import Container
from .digest import week_bounds
from .schemas import (
    ConflictView,
    DecisionView,
    DigestView,
    HealthView,
    IngestResponse,
    MeetingCreate,
    MeetingView,
    NotificationView,
    ReminderView,
    ReviewPacket,
    ReviewResult,
    ReviewSubmission,
    ReviewSummary,
    StatsView,
    TaskView,
)


def get_container(request: Request) -> Container:
    return request.app.state.container


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container = Container.build(resolved)
        app.state.container = container
        container.start()
        try:
            yield
        finally:
            container.close()

    app = FastAPI(
        title=resolved.app_name,
        version="0.1.0",
        description=(
            "Approval-gated meeting execution pipeline with decision memory, "
            "conflict detection, tracker posting, reminders, and weekly digests."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health", response_model=HealthView, tags=["system"])
    def health(request: Request) -> HealthView:
        return HealthView(**get_container(request).health())

    @app.get("/api/stats", response_model=StatsView, tags=["system"])
    def stats(request: Request) -> StatsView:
        return get_container(request).memory.stats()

    @app.post("/api/meetings", response_model=IngestResponse, tags=["meetings"])
    async def ingest_meeting(body: MeetingCreate, request: Request) -> IngestResponse:
        container = get_container(request)
        meeting_id = body.meeting_id or f"mtg_{uuid4().hex[:12]}"
        if container.memory.get_meeting(meeting_id):
            raise HTTPException(status_code=409, detail=f"meeting already exists: {meeting_id}")
        meeting = body.model_copy(update={"meeting_id": meeting_id})
        thread_id = f"thr_{uuid4().hex[:12]}"
        try:
            await run_in_threadpool(container.pipeline.start, meeting, thread_id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"pipeline failed: {exc}") from exc
        packet = await run_in_threadpool(container.pipeline.get_packet, thread_id)
        if packet is None:
            raise HTTPException(status_code=500, detail="pipeline did not produce a review packet")
        return IngestResponse(
            thread_id=thread_id,
            status="awaiting_approval",
            review=packet,
        )

    @app.get("/api/meetings", response_model=list[MeetingView], tags=["meetings"])
    def list_meetings(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[MeetingView]:
        return get_container(request).memory.list_meetings(limit=limit)

    @app.get("/api/meetings/{meeting_id}", response_model=MeetingView, tags=["meetings"])
    def get_meeting(meeting_id: str, request: Request) -> MeetingView:
        meeting = get_container(request).memory.get_meeting(meeting_id)
        if not meeting:
            raise HTTPException(status_code=404, detail="meeting not found")
        return meeting

    @app.get("/api/reviews", response_model=list[ReviewSummary], tags=["reviews"])
    def list_reviews(
        request: Request,
        status: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[ReviewSummary]:
        return get_container(request).memory.list_reviews(status=status, limit=limit)

    @app.get("/api/reviews/{thread_id}", response_model=ReviewPacket, tags=["reviews"])
    async def get_review(thread_id: str, request: Request) -> ReviewPacket:
        container = get_container(request)
        packet = await run_in_threadpool(container.pipeline.get_packet, thread_id)
        if packet is None:
            raise HTTPException(status_code=404, detail="review not found")
        return packet

    @app.post("/api/reviews/{thread_id}", response_model=ReviewResult, tags=["reviews"])
    async def submit_review(
        thread_id: str, body: ReviewSubmission, request: Request
    ) -> ReviewResult:
        container = get_container(request)
        packet = await run_in_threadpool(container.pipeline.get_packet, thread_id)
        if packet is None:
            raise HTTPException(status_code=404, detail="review not found")
        if body.packet_hash != packet.packet_hash:
            raise HTTPException(status_code=409, detail="stale packet_hash; reload the review")
        try:
            result = await run_in_threadpool(container.pipeline.resume, thread_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"approval failed: {exc}") from exc

        if result.get("rejected"):
            return ReviewResult(
                thread_id=thread_id,
                status="rejected",
                message="Review rejected. Nothing was written to memory or posted.",
            )

        posted = result.get("posted", [])
        approved_tasks = result.get("approved_tasks", [])
        skipped = len([task for task in approved_tasks if task.status == "skipped"])
        decisions_saved = len(result.get("approved_decisions", []))
        return ReviewResult(
            thread_id=thread_id,
            status="approved",
            decisions_saved=decisions_saved,
            tasks_posted=len(posted),
            tasks_skipped=skipped,
            superseded=result.get("superseded", []),
            reminders=result.get("reminders", []),
            posted_tasks=posted,
            message=(
                f"Approved {decisions_saved} decision(s), posted {len(posted)} task(s), "
                f"skipped {skipped} duplicate(s)."
            ),
        )

    @app.get("/api/decisions", response_model=list[DecisionView], tags=["memory"])
    def list_decisions(
        request: Request,
        status: str | None = None,
        meeting_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> list[DecisionView]:
        return get_container(request).memory.list_decisions(
            status=status, meeting_id=meeting_id, limit=limit
        )

    @app.get("/api/decisions/{decision_id}", response_model=DecisionView, tags=["memory"])
    def get_decision(decision_id: str, request: Request) -> DecisionView:
        decision = get_container(request).memory.get_decision(decision_id)
        if not decision:
            raise HTTPException(status_code=404, detail="decision not found")
        return decision

    @app.get("/api/conflicts", response_model=list[ConflictView], tags=["memory"])
    def list_conflicts(
        request: Request,
        meeting_id: str | None = None,
        decision_id: str | None = None,
        relation: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> list[ConflictView]:
        return get_container(request).memory.list_conflicts(
            limit=limit,
            meeting_id=meeting_id,
            decision_id=decision_id,
            relation=relation,
        )

    @app.get("/api/tasks", response_model=list[TaskView], tags=["execution"])
    def list_tasks(
        request: Request,
        status: str | None = None,
        owner_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> list[TaskView]:
        return get_container(request).memory.list_tasks(
            status=status, owner_id=owner_id, limit=limit
        )

    @app.get("/api/tasks/{task_id}", response_model=TaskView, tags=["execution"])
    def get_task(task_id: str, request: Request) -> TaskView:
        task = get_container(request).memory.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        return task

    @app.get("/api/reminders", response_model=list[ReminderView], tags=["execution"])
    def list_reminders(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> list[ReminderView]:
        return get_container(request).memory.list_reminders(limit=limit)

    @app.get("/api/digests", response_model=list[DigestView], tags=["execution"])
    def list_digests(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[DigestView]:
        return get_container(request).memory.list_digests(limit=limit)

    @app.get("/api/digests/preview", response_model=DigestView, tags=["execution"])
    async def preview_digest(
        request: Request,
        owner_id: str,
        week_start: date | None = None,
        week_end: date | None = None,
    ) -> DigestView:
        start, end = (
            (week_start, week_end)
            if week_start and week_end
            else week_bounds()
        )
        digest = await run_in_threadpool(
            get_container(request).digests.build_for_owner,
            owner_id,
            start,
            end,
            save=False,
        )
        if digest is None:
            raise HTTPException(status_code=404, detail="no digestable tasks for this owner/week")
        return digest

    @app.get("/api/notifications", response_model=list[NotificationView], tags=["execution"])
    def list_notifications(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[NotificationView]:
        return get_container(request).memory.list_notifications(limit=limit)

    @app.post("/api/jobs/reminders/run", response_model=list[dict], tags=["jobs"])
    async def run_reminders(request: Request) -> list[dict[str, Any]]:
        return await run_in_threadpool(get_container(request).scheduler.run_reminders_now)

    @app.post("/api/jobs/digest/run", response_model=list[DigestView], tags=["jobs"])
    async def run_digest(
        request: Request,
        week_start: date | None = None,
        week_end: date | None = None,
    ) -> list[DigestView]:
        start, end = (
            (week_start, week_end)
            if week_start and week_end
            else week_bounds()
        )
        return await run_in_threadpool(
            get_container(request).digests.run_weekly, start, end
        )

    return app


app = create_app()
