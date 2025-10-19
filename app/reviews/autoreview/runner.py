"""Autoreview check runner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .checks import get_check_by_id, get_enabled_checks
from .context import CheckContext
from .decision import AutoreviewDecision
from .utils.redirect import get_redirect_aliases
from .utils.user import normalize_to_lookup

if TYPE_CHECKING:
    from reviews.models import EditorProfile, PendingRevision
    from reviews.services import WikiClient


def run_checks_pipeline(
    revision: PendingRevision,
    client: WikiClient,
    profile: EditorProfile | None,
    *,
    auto_groups: dict[str, str],
    blocking_categories: dict[str, str],
    redirect_aliases: list[str],
) -> dict:
    """Run all enabled checks in order, stopping at blocking/approving checks."""
    context = CheckContext(
        revision=revision,
        client=client,
        profile=profile,
        auto_groups=auto_groups,
        blocking_categories=blocking_categories,
        redirect_aliases=redirect_aliases,
    )

    configuration = revision.page.wiki.configuration
    checks_to_run = get_enabled_checks(configuration)

    tests = []
    for check_info in checks_to_run:
        result = check_info["function"](context)
        tests.append(
            {
                "id": result.check_id,
                "title": result.check_title,
                "status": result.status,
                "message": result.message,
            }
        )

        if result.should_stop:
            return {"tests": tests, "decision": result.decision}

        if (
            result.check_id == "article-to-redirect-conversion"
            and result.status == "ok"
            and profile
            and profile.is_autopatrolled
        ):
            return {
                "tests": tests,
                "decision": AutoreviewDecision(
                    status="approve",
                    label="Would be auto-approved",
                    reason="The user has autopatrol rights that allow auto-approval.",
                ),
            }

    return {
        "tests": tests,
        "decision": AutoreviewDecision(
            status="manual",
            label="Requires human review",
            reason="In dry-run mode the edit would not be approved automatically.",
        ),
    }


def run_single_check(
    check_id: str,
    revision: PendingRevision,
    client: WikiClient | None = None,
    profile: EditorProfile | None = None,
) -> dict:
    """Run a specific check against a revision."""
    check_info = get_check_by_id(check_id)
    if not check_info:
        raise ValueError(f"Check with ID '{check_id}' not found")

    if client is None:
        from reviews.services import WikiClient

        client = WikiClient(revision.page.wiki)

    configuration = revision.page.wiki.configuration
    auto_groups = normalize_to_lookup(configuration.auto_approved_groups)
    blocking_categories = normalize_to_lookup(configuration.blocking_categories)
    redirect_aliases = get_redirect_aliases(revision.page.wiki)

    context = CheckContext(
        revision=revision,
        client=client,
        profile=profile,
        auto_groups=auto_groups,
        blocking_categories=blocking_categories,
        redirect_aliases=redirect_aliases,
    )

    result = check_info["function"](context)

    return {
        "check_id": result.check_id,
        "check_title": result.check_title,
        "status": result.status,
        "message": result.message,
        "decision": (
            {
                "status": result.decision.status,
                "label": result.decision.label,
                "reason": result.decision.reason,
            }
            if result.decision
            else None
        ),
    }
