"""Insertion points for later usage enforcement; no quotas are enforced in this phase."""

from uuid import UUID


def check_generation_quota(user_id: UUID | None) -> None:
    """Phase 4 hook: check the caller's quota before a provider request."""
    # Intentionally a no-op until quota enforcement is explicitly implemented.
    return None
