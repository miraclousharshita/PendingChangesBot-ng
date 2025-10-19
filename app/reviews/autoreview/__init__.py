"""Logic for simulating automatic review decisions for pending revisions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .decision import AutoreviewDecision
from .runner import run_checks_pipeline, run_single_check
from .utils.isbn import find_invalid_isbns, validate_isbn_10, validate_isbn_13
from .utils.ores import get_ores_scores
from .utils.redirect import get_redirect_aliases, is_redirect
from .utils.similarity import is_addition_superseded
from .utils.user import is_bot_user, normalize_to_lookup
from .utils.wikitext import extract_additions, get_parent_wikitext, normalize_wikitext

if TYPE_CHECKING:
    from reviews.models import PendingPage, PendingRevision

__all__ = [
    "AutoreviewDecision",
    "run_autoreview_for_page",
    "run_single_check",
    "find_invalid_isbns",
    "validate_isbn_10",
    "validate_isbn_13",
    "is_redirect",
    "get_redirect_aliases",
    "normalize_wikitext",
    "extract_additions",
    "get_parent_wikitext",
    "normalize_to_lookup",
    "is_addition_superseded",
    "is_bot_user",
]

_validate_isbn_10 = validate_isbn_10
_validate_isbn_13 = validate_isbn_13
_find_invalid_isbns = find_invalid_isbns
_is_redirect = is_redirect
_normalize_wikitext = normalize_wikitext
_extract_additions = extract_additions
_get_parent_wikitext = get_parent_wikitext
_is_addition_superseded = is_addition_superseded
_is_bot_user = is_bot_user


def _check_ores_scores(
    revision: PendingRevision,
    damaging_threshold: float,
    goodfaith_threshold: float,
) -> dict:
    """Backward compatibility wrapper for _check_ores_scores."""
    check_damaging = damaging_threshold > 0
    check_goodfaith = goodfaith_threshold > 0

    if not check_damaging and not check_goodfaith:
        return {
            "should_block": False,
            "test": {
                "id": "ores-scores",
                "title": "ORES edit quality scores",
                "status": "skip",
                "message": "ORES checks are disabled (thresholds set to 0).",
            },
        }

    damaging_prob, goodfaith_prob = get_ores_scores(revision, check_damaging, check_goodfaith)

    if damaging_prob is None and goodfaith_prob is None:
        return {
            "should_block": True,
            "test": {
                "id": "ores-scores",
                "title": "ORES edit quality check failed",
                "status": "fail",
                "message": "Could not verify ORES edit quality scores.",
            },
        }

    if damaging_threshold > 0 and damaging_prob is not None:
        if damaging_prob > damaging_threshold:
            return {
                "should_block": True,
                "test": {
                    "id": "ores-scores",
                    "title": "ORES edit quality scores",
                    "status": "fail",
                    "message": (
                        f"ORES damaging score ({damaging_prob:.3f}) "
                        f"exceeds threshold ({damaging_threshold:.3f})."
                    ),
                },
            }

    if goodfaith_threshold > 0 and goodfaith_prob is not None:
        if goodfaith_prob < goodfaith_threshold:
            return {
                "should_block": True,
                "test": {
                    "id": "ores-scores",
                    "title": "ORES edit quality scores",
                    "status": "fail",
                    "message": (
                        f"ORES goodfaith score ({goodfaith_prob:.3f}) "
                        f"is below threshold ({goodfaith_threshold:.3f})."
                    ),
                },
            }

    messages = []
    if damaging_threshold > 0 and damaging_prob is not None:
        messages.append(f"damaging: {damaging_prob:.3f}")
    if goodfaith_threshold > 0 and goodfaith_prob is not None:
        messages.append(f"goodfaith: {goodfaith_prob:.3f}")

    return {
        "should_block": False,
        "test": {
            "id": "ores-scores",
            "title": "ORES edit quality scores",
            "status": "ok",
            "message": f"ORES scores are within acceptable thresholds ({', '.join(messages)}).",
        },
    }


def _evaluate_revision(
    revision: PendingRevision,
    client,
    profile,
    *,
    auto_groups: dict[str, str],
    blocking_categories: dict[str, str],
    redirect_aliases: list[str],
) -> dict:
    """Backward compatibility wrapper for _evaluate_revision."""
    return run_checks_pipeline(
        revision,
        client,
        profile,
        auto_groups=auto_groups,
        blocking_categories=blocking_categories,
        redirect_aliases=redirect_aliases,
    )


def run_autoreview_for_page(page: PendingPage) -> list[dict]:
    """Run the configured autoreview checks for each pending revision of a page."""
    from reviews.models import EditorProfile
    from reviews.services import WikiClient

    revisions = list(page.revisions.exclude(revid=page.stable_revid).order_by("timestamp", "revid"))
    if not revisions:
        return []

    usernames = {rev.user_name for rev in revisions if rev.user_name}
    profiles = (
        {
            profile.username: profile
            for profile in EditorProfile.objects.filter(wiki=page.wiki, username__in=usernames)
        }
        if usernames
        else {}
    )

    configuration = page.wiki.configuration
    auto_groups = normalize_to_lookup(configuration.auto_approved_groups)
    blocking_categories = normalize_to_lookup(configuration.blocking_categories)
    redirect_aliases = get_redirect_aliases(page.wiki)
    client = WikiClient(page.wiki)

    results = []
    for revision in revisions:
        profile = profiles.get(revision.user_name or "")
        revision_result = run_checks_pipeline(
            revision,
            client,
            profile,
            auto_groups=auto_groups,
            blocking_categories=blocking_categories,
            redirect_aliases=redirect_aliases,
        )
        results.append(
            {
                "revid": revision.revid,
                "tests": revision_result["tests"],
                "decision": {
                    "status": revision_result["decision"].status,
                    "label": revision_result["decision"].label,
                    "reason": revision_result["decision"].reason,
                },
            }
        )

    return results
