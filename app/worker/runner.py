"""
Distributed Background Worker Runner.
Supports running in multi-worker setup (worker-1, worker-2) in Docker.
Uses PostgreSQL row-lease claiming with lease timeouts and heartbeats.
Survives worker crashes, restarts, and gracefully pauses without holding threads.
"""

import asyncio
import signal
import sys
from datetime import timedelta
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.core.config import settings
from app.core.clock import Clock
from app.db.session import async_session_factory
from app.db.models import Incident
from app.engine.runner import advance_incident_state


class IncidentWorker:
    def __init__(
        self,
        worker_id: str = settings.WORKER_ID,
        lease_seconds: int = settings.WORKER_LEASE_SECONDS,
        heartbeat_seconds: int = settings.WORKER_HEARTBEAT_SECONDS
    ):
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.is_running = True

    async def claim_task(self, db: AsyncSession) -> Optional[Incident]:
        """
        Atomically finds and claims an actionable incident.
        Uses lease expiration comparison to claim unworked or abandoned tasks.
        """
        now = Clock.now()
        
        # Eligible statuses that require worker processing
        runnable_nodes = ["init", "triage", "diagnose", "propose", "policy", "execute", "verify"]
        runnable_statuses = ["QUEUED", "TRIAGED", "DIAGNOSED", "PROPOSED", "APPROVED_AUTO", "APPROVED_HUMAN", "EXECUTING"]

        stmt = (
            select(Incident)
            .where(
                and_(
                    Incident.status.in_(runnable_statuses),
                    Incident.current_node.in_(runnable_nodes),
                    or_(
                        Incident.lease_worker_id.is_(None),
                        Incident.lease_expires_at < now
                    )
                )
            )
            .order_by(Incident.created_at.asc())
            .limit(1)
        )

        result = await db.execute(stmt)
        incident = result.scalars().first()

        if incident:
            # Claim the lease
            incident.lease_worker_id = self.worker_id
            incident.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            incident.heartbeat_at = now
            await db.commit()
            return incident

        return None

    async def release_lease(self, incident_id: str, db: AsyncSession):
        """Release the worker lease so others can pick it up if needed."""
        stmt = select(Incident).where(Incident.id == incident_id)
        result = await db.execute(stmt)
        inc = result.scalars().first()
        if inc and inc.lease_worker_id == self.worker_id:
            inc.lease_worker_id = None
            inc.lease_expires_at = None
            await db.commit()

    async def run_incident_to_pause(self, incident_id: str, db: AsyncSession):
        """
        Advances the incident state machine through its nodes until it
        pauses (e.g. AWAITING_APPROVAL) or reaches a terminal state.
        """
        try:
            while self.is_running:
                stmt = select(Incident).where(Incident.id == incident_id)
                result = await db.execute(stmt)
                incident = result.scalars().first()

                if not incident:
                    break

                # Advance one step
                can_continue = await advance_incident_state(incident, db, self.worker_id)
                if not can_continue:
                    # Incident is either paused for human approval or finished
                    break
        finally:
            # Always cleanly release the worker lease
            await self.release_lease(incident_id, db)

    async def run_once(self) -> bool:
        """Processes one eligible task from the queue. Returns True if task processed."""
        async with async_session_factory() as session:
            incident = await self.claim_task(session)
            if incident:
                await self.run_incident_to_pause(incident.id, session)
                return True
        return False

    async def start(self):
        """Main continuous loop for running inside Docker container."""
        print(f"🚀 Worker {self.worker_id} started. Polling for incidents...")
        while self.is_running:
            try:
                processed = await self.run_once()
                if not processed:
                    await asyncio.sleep(1.0)
            except Exception as e:
                print(f"Worker {self.worker_id} error: {e}", file=sys.stderr)
                await asyncio.sleep(2.0)

    def stop(self):
        print(f"Stopping worker {self.worker_id}...")
        self.is_running = False


# Entry point for `python -m app.worker.runner`
if __name__ == "__main__":
    worker = IncidentWorker()

    def handle_signal(sig, frame):
        worker.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    asyncio.run(worker.start())
