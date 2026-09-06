"""Regrade event repository -- the read side of "why did this number move".

Queries by ``suite_id``, never ``suite_name``. Both columns exist and they are
not interchangeable: a suite deleted and recreated under the same name would
serve the previous suite's history on the new suite's page. The name is for
``regrade_history.py``, where an operator types what they have.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from beacon_storage.models.regrade_events import RegradeEvent

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class RegradeEventRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_suite(self, *, suite_id: UUID, limit: int = 100) -> list[RegradeEvent]:
        """Every recorded regrade of this suite, newest first.

        Newest by ``created_at`` and then ``id``. The tie-break is not
        decoration: ``created_at`` defaults to ``now()``, which is
        transaction-scoped, so two events written in one transaction share a
        timestamp exactly. Ordering on the timestamp alone leaves their order
        to the planner, and ids are UUIDv7 -- write-ordered -- so they break
        the tie the way a reader expects.
        """
        return list(
            self.session.scalars(
                select(RegradeEvent)
                .where(RegradeEvent.suite_id == suite_id)
                .order_by(RegradeEvent.created_at.desc(), RegradeEvent.id.desc())
                .limit(limit)
            )
        )

    def get_for_suite(self, *, suite_id: UUID, event_id: UUID) -> RegradeEvent | None:
        """One event, scoped to the suite whose page is asking for it.

        The suite is part of the lookup rather than checked afterwards. A bare
        ``get(event_id)`` would serve any event from any suite the caller can
        reach, which turns a benchmark's History page into a cross-benchmark
        read whenever an id is guessed or pasted. RLS still bounds this to the
        caller's teams; this bounds it to the page.
        """
        return self.session.scalars(
            select(RegradeEvent).where(
                RegradeEvent.id == event_id,
                RegradeEvent.suite_id == suite_id,
            )
        ).one_or_none()
