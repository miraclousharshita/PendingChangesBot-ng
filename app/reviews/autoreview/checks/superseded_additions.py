"""Superseded additions check."""

from __future__ import annotations

import logging

from ..base import CheckResult
from ..context import CheckContext
from ..decision import AutoreviewDecision
from ..utils.similarity import is_addition_superseded

logger = logging.getLogger(__name__)


def check_superseded_additions(context: CheckContext) -> CheckResult:
    """Check if additions from this revision have been superseded."""
    try:
        from reviews.models import PendingRevision

        stable_revision = PendingRevision.objects.filter(
            page=context.revision.page, revid=context.revision.page.stable_revid
        ).first()

        if stable_revision:
            current_stable_wikitext = stable_revision.get_wikitext()
            threshold = context.revision.page.wiki.configuration.superseded_similarity_threshold

            if is_addition_superseded(context.revision, current_stable_wikitext, threshold):
                return CheckResult(
                    check_id="superseded-additions",
                    check_title="Superseded additions",
                    status="ok",
                    message=(
                        "The additions from this revision have been superseded "
                        "or removed in the latest version."
                    ),
                    decision=AutoreviewDecision(
                        status="approve",
                        label="Would be auto-approved",
                        reason=(
                            "The additions from this revision have been superseded "
                            "or removed in the latest version."
                        ),
                    ),
                    should_stop=True,
                )

        return CheckResult(
            check_id="superseded-additions",
            check_title="Superseded additions",
            status="not_ok",
            message="The additions from this revision are still relevant.",
        )
    except Exception as e:
        logger.error(
            f"Error checking superseded additions for revision {context.revision.revid}: {e}"
        )
        return CheckResult(
            check_id="superseded-additions",
            check_title="Superseded additions check",
            status="not_ok",
            message="Could not verify if additions were superseded.",
        )
