"""SuiteService create, add, replace, and list operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from beacon_storage.repository.suites import SuiteRepo
from sqlalchemy.exc import IntegrityError

from beacon_registry.errors import DuplicateSuiteError, SuiteNotFoundError

if TYPE_CHECKING:
    from uuid import UUID

    from beacon_storage.models.suites import Suite
    from sqlalchemy.orm import Session


class SuiteService:
    """Service boundary for project-scoped suite operations."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = SuiteRepo(session)

    def create(
        self,
        *,
        project_id: UUID,
        team_id: UUID,
        name: str,
        description: str,
        method: str,
        suite_metadata: dict[str, Any],
        created_by: UUID,
    ) -> Suite:
        """Create a suite, mapping project-name uniqueness to a domain error."""
        try:
            return self.repo.create(
                project_id=project_id,
                team_id=team_id,
                name=name,
                description=description,
                method=method,
                suite_metadata=suite_metadata,
                created_by=created_by,
            )
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateSuiteError(
                f"suite name '{name}' already exists in project {project_id}"
            ) from exc

    def get(self, suite_id: UUID) -> Suite | None:
        """Return the suite with this id, or None if it does not exist."""
        return self.repo.get(suite_id)

    def get_or_raise(self, suite_id: UUID) -> Suite:
        """Return the suite with this id or raise SuiteNotFoundError."""
        suite = self.repo.get(suite_id)
        if suite is None:
            raise SuiteNotFoundError(f"suite {suite_id} not found")
        return suite

    def get_by_project_and_name(self, project_id: UUID, name: str) -> Suite | None:
        """Look up a suite by project and name, returning None when absent."""
        return self.repo.get_by_project_and_name(project_id, name)

    def get_by_project_and_name_or_raise(self, project_id: UUID, name: str) -> Suite:
        """Look up a suite by project and name or raise SuiteNotFoundError."""
        suite = self.repo.get_by_project_and_name(project_id, name)
        if suite is None:
            raise SuiteNotFoundError(f"no suite named '{name}' in project {project_id}")
        return suite

    def list_for_project(self, project_id: UUID) -> list[Suite]:
        """List all suites belonging to the given project."""
        return self.repo.list_for_project(project_id)

    def add_items(self, *, suite_id: UUID, item_ids: list[UUID]) -> int:
        """Add items idempotently and return the count of new joins."""
        return self.repo.add_items(suite_id=suite_id, item_ids=item_ids)

    def replace_items(self, *, suite_id: UUID, item_ids: list[UUID]) -> None:
        """Replace the suite item set with the supplied item ids."""
        existing_ids = set(self.repo.list_item_ids(suite_id))
        requested_ids = set(item_ids)
        to_remove = list(existing_ids - requested_ids)
        to_add = list(requested_ids - existing_ids)
        if to_remove:
            self.repo.remove_items(suite_id=suite_id, item_ids=to_remove)
        if to_add:
            self.repo.add_items(suite_id=suite_id, item_ids=to_add)

    def list_item_ids(self, suite_id: UUID) -> list[UUID]:
        """List the eval-item ids currently joined to this suite."""
        return self.repo.list_item_ids(suite_id)
