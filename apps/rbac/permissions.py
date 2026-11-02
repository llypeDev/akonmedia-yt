"""Role checks used by views, templates and the API.

Everything goes through :func:`has_at_least`, so a policy change is one edit
rather than a hunt through conditionals.
"""
from .roles import RANK, Role


def rank_of(user) -> int:
    if not user or not user.is_authenticated:
        return 0
    if user.is_superuser:
        return RANK[Role.ADMIN]
    return RANK.get(user.role, RANK[Role.MEMBER])


def has_at_least(user, role) -> bool:
    return rank_of(user) >= RANK[role]


def can_upload(user) -> bool:
    return has_at_least(user, Role.MEMBER)


def can_manage_own_media(user) -> bool:
    return has_at_least(user, Role.MEMBER)


def can_curate_others(user) -> bool:
    """Edit or remove media that belongs to somebody else."""
    return has_at_least(user, Role.EDITOR)


def can_moderate_comments(user) -> bool:
    return has_at_least(user, Role.EDITOR)


def can_manage_users(user) -> bool:
    return has_at_least(user, Role.MANAGER)


def can_configure_portal(user) -> bool:
    return has_at_least(user, Role.ADMIN)
