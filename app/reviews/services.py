"""Service layer for interacting with Wikimedia projects via Pywikibot."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

import mwparserfromhell
import pywikibot
from django.db import transaction
from django.utils import timezone as dj_timezone
from pywikibot.data.superset import SupersetQuery

from .models import EditorProfile, PendingPage, PendingRevision, Wiki

logger = logging.getLogger(__name__)

os.environ.setdefault("PYWIKIBOT2_NO_USER_CONFIG", "1")
os.environ.setdefault("PYWIKIBOT_NO_USER_CONFIG", "2")


@dataclass
class RevisionPayload:
    revid: int
    parentid: int | None
    user: str | None
    userid: int | None
    timestamp: datetime
    comment: str
    sha1: str
    tags: list[str]
    superset_data: dict | None = None


class WikiClient:
    """Client responsible for synchronising data for a wiki."""

    def __init__(self, wiki: Wiki):
        self.wiki = wiki
        self.site = pywikibot.Site(code=wiki.code, fam=wiki.family)

    def has_manual_unapproval(self, page_title: str, revid: int) -> bool:
        """Check if the most recent review action for a revision is an un-approval."""
        try:
            request = self.site.simple_request(
                action="query",
                list="logevents",
                letype="review",
                letitle=page_title,
                lelimit=50,
                leprop="ids|type|details|timestamp|user",
                formatversion=2,
            )
            response = request.submit()
            log_events = response.get("query", {}).get("logevents", [])

            for event in log_events:
                params = event.get("params", {})
                event_revid = params.get("0")

                if event_revid == revid:
                    action = event.get("action")
                    if action in ("unapprove", "unapprove2"):
                        logger.info(
                            "Revision %s was manually un-approved (action: %s) at %s",
                            revid,
                            action,
                            event.get("timestamp"),
                        )
                        return True
                    else:
                        logger.info(
                            "Revision %s has review action '%s' at %s (not un-approved)",
                            revid,
                            action,
                            event.get("timestamp"),
                        )
                        return False

            return False
        except Exception:  # pragma: no cover - network failure fallback
            logger.exception(
                "Failed to check review log for page %s, revision %s",
                page_title,
                revid,
            )
            return False

    def is_user_blocked_after_edit(self, username: str, edit_timestamp: datetime) -> bool:
        """Check if user was blocked after making an edit."""
        # Extract year from timestamp for cache efficiency
        year = edit_timestamp.year
        return was_user_blocked_after(self.wiki.code, self.wiki.family, username, year)

    def get_rendered_html(self, revid: int) -> str:
        """Fetch the rendered HTML for a specific revision."""
        if not revid:
            return ""

        try:
            revision = PendingRevision.objects.get(page__wiki=self.wiki, revid=revid)
            if revision.rendered_html:
                return revision.rendered_html
        except PendingRevision.DoesNotExist:
            revision = None
        except Exception:
            revision = None

        request = self.site.simple_request(
            action="parse",
            oldid=revid,
            prop="text",
            formatversion=2,
        )
        try:
            response = request.submit()
            html = response.get("parse", {}).get("text", "")
            html_content = html if isinstance(html, str) else ""
            if revision and html_content:
                revision.rendered_html = html_content
                revision.save(update_fields=["rendered_html"])
            return html_content
        except Exception:
            return ""

    def fetch_pending_pages(self, limit: int = 10000) -> list[PendingPage]:
        """Fetch the pending pages using Superset and cache them in the database."""

        limit = int(limit)
        if limit <= 0:
            return []

        sql_query = f"""
SELECT
   page_title,
   page_namespace,
   page_is_redirect,
   fp_page_id,
   fp_pending_since,
   fp_stable,
   rev_id,
   rev_timestamp,
   rev_len,
   rev_parent_id,
   rev_deleted,
   rev_sha1,
   comment_text,
   a.actor_name,
   a.actor_user,
   group_concat(DISTINCT(ctd_name)) AS change_tags,
   group_concat(DISTINCT(ug_group)) AS user_groups,
   group_concat(DISTINCT(ufg_group)) AS user_former_groups,
   group_concat(DISTINCT(cl_to)) AS page_categories,
   rc_bot,
   rc_patrolled,
   pp_value as wikibase_item
FROM
   (SELECT fp.* FROM page,flaggedpages as fp
   WHERE fp_page_id=page_id AND page_namespace=0 AND fp_pending_since IS NOT NULL
   ORDER BY fp_pending_since DESC LIMIT {limit}) AS fp,
   revision AS r
       LEFT JOIN change_tag ON r.rev_id=ct_rev_id
       LEFT JOIN change_tag_def ON ct_tag_id = ctd_id
       LEFT JOIN recentchanges ON rc_this_oldid = r.rev_id AND rc_source="mw.edit"
   ,
   page AS p
       LEFT JOIN categorylinks ON cl_from = page_id
       LEFT JOIN page_props ON pp_page = page_id AND pp_propname="wikibase_item",
   comment_revision,
   actor_revision AS a
   LEFT JOIN user_groups ON a.actor_user=ug_user
   LEFT JOIN user_former_groups ON a.actor_user=ufg_user
WHERE
   fp_pending_since IS NOT NULL
   AND r.rev_page=fp_page_id
   AND page_id=fp_page_id
   AND page_namespace=0
   AND r.rev_id>=fp_stable
   AND r.rev_actor=a.actor_id
   AND r.rev_comment_id=comment_id
GROUP BY r.rev_id
ORDER BY fp_pending_since, rev_id DESC
"""

        superset = SupersetQuery(site=self.site)
        payload = superset.query(sql_query)
        pages: list[PendingPage] = []
        pages_by_id: dict[int, PendingPage] = {}

        with transaction.atomic():
            PendingRevision.objects.filter(page__wiki=self.wiki).delete()
            PendingPage.objects.filter(wiki=self.wiki).delete()
            for entry in payload:
                pageid = entry.get("fp_page_id")
                try:
                    pageid_int = int(pageid)
                except (TypeError, ValueError):
                    continue
                page_categories = parse_superset_list(entry.get("page_categories"))

                page = pages_by_id.get(pageid_int)
                if page is None:
                    pending_since = parse_superset_timestamp(entry.get("fp_pending_since"))
                    page = PendingPage.objects.create(
                        wiki=self.wiki,
                        pageid=pageid_int,
                        title=entry.get("page_title", ""),
                        stable_revid=int(entry.get("fp_stable") or 0),
                        pending_since=pending_since,
                        categories=page_categories,
                        wikidata_id=entry.get("wikibase_item", ""),
                    )
                    pages_by_id[pageid_int] = page
                    pages.append(page)
                elif page_categories != (page.categories or []):
                    page.categories = page_categories
                    page.save(update_fields=["categories"])

                revid = entry.get("rev_id")
                try:
                    revid_int = int(revid)
                except (TypeError, ValueError):
                    continue

                superset_revision_timestamp = parse_superset_timestamp(entry.get("rev_timestamp"))
                if superset_revision_timestamp is None:
                    superset_revision_timestamp = dj_timezone.now()

                payload_entry = RevisionPayload(
                    revid=revid_int,
                    parentid=_parse_optional_int(entry.get("rev_parent_id")),
                    user=entry.get("actor_name"),
                    userid=_parse_optional_int(entry.get("actor_user")),
                    timestamp=superset_revision_timestamp,
                    comment=entry.get("comment_text", "") or "",
                    sha1=entry.get("rev_sha1", "") or "",
                    tags=parse_superset_list(entry.get("change_tags")),
                    superset_data=_prepare_superset_metadata(entry),
                )
                self._save_revision(page, payload_entry)

        return pages

    def _save_revision(self, page: PendingPage, payload: RevisionPayload) -> PendingRevision | None:
        existing_page = (
            PendingPage.objects.filter(pk=page.pk).only("id").first() if page.pk else None
        )
        if existing_page is None:
            logger.warning(
                "Pending page %s was deleted before saving revision %s", page.pk, payload.revid
            )
            return None

        age = dj_timezone.now() - payload.timestamp
        defaults = {
            "parentid": payload.parentid,
            "user_name": payload.user or "",
            "user_id": payload.userid,
            "timestamp": payload.timestamp,
            "age_at_fetch": age,
            "sha1": payload.sha1,
            "comment": payload.comment,
            "change_tags": payload.tags,
            "wikitext": "",
        }
        if payload.superset_data is not None:
            defaults["superset_data"] = payload.superset_data

        revision, _ = PendingRevision.objects.update_or_create(
            page=existing_page,
            revid=payload.revid,
            defaults=defaults,
        )
        if payload.user:
            self.ensure_editor_profile(payload.user, payload.superset_data)
        return revision

    def ensure_editor_profile(
        self, username: str, superset_data: dict | None = None
    ) -> EditorProfile:
        profile, created = EditorProfile.objects.get_or_create(
            wiki=self.wiki,
            username=username,
            defaults={
                "usergroups": [],
                "is_blocked": False,
                "is_bot": False,
                "is_former_bot": False,
                "is_autopatrolled": False,
                "is_autoreviewed": False,
            },
        )
        if not superset_data:
            return profile

        autoreviewed_groups = {"autoreview", "autoreviewer", "editor", "reviewer", "sysop", "bot"}
        groups = sorted(superset_data.get("user_groups") or [])
        former_groups = sorted(superset_data.get("user_former_groups") or [])

        profile.usergroups = groups
        profile.is_bot = "bot" in groups or bool(superset_data.get("rc_bot"))
        profile.is_former_bot = "bot" in former_groups
        profile.is_autopatrolled = "autopatrolled" in groups
        profile.is_autoreviewed = bool(autoreviewed_groups & set(groups))
        profile.is_blocked = bool(superset_data.get("user_blocked", False))
        profile.save(
            update_fields=[
                "usergroups",
                "is_blocked",
                "is_bot",
                "is_former_bot",
                "is_autopatrolled",
                "is_autoreviewed",
                "fetched_at",
            ]
        )
        return profile

    def refresh(self) -> list[PendingPage]:
        return self.fetch_pending_pages()


def parse_categories(wikitext: str) -> list[str]:
    code = mwparserfromhell.parse(wikitext or "")
    categories: list[str] = []
    for link in code.filter_wikilinks():
        target = str(link.title).strip()
        if target.lower().startswith("category:"):
            categories.append(target.split(":", 1)[-1])
    return sorted(set(categories))


def parse_superset_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        timestamp = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            timestamp = datetime.fromisoformat(normalized.replace(" ", "T"))
        except ValueError:
            if normalized.isdigit() and len(normalized) == 14:
                try:
                    timestamp = datetime.strptime(normalized, "%Y%m%d%H%M%S")
                except ValueError:
                    logger.warning("Unable to parse Superset timestamp: %s", value)
                    return None
                else:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
            else:
                logger.warning("Unable to parse Superset timestamp: %s", value)
                return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp


def parse_superset_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item and item.strip()]


def _parse_optional_int(value) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _prepare_superset_metadata(entry: dict) -> dict:
    metadata = dict(entry)
    for key in (
        "change_tags",
        "user_groups",
        "user_former_groups",
        "page_categories",
    ):
        if key in metadata and isinstance(metadata[key], str):
            metadata[key] = parse_superset_list(metadata[key])
    if "actor_user" in metadata:
        metadata["actor_user"] = _parse_optional_int(metadata.get("actor_user"))
    if "rc_bot" in metadata:
        metadata["rc_bot"] = _parse_superset_bool(metadata.get("rc_bot"))
    if "rc_patrolled" in metadata:
        metadata["rc_patrolled"] = _parse_superset_bool(metadata.get("rc_patrolled"))
    return metadata


def _parse_superset_bool(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "null"}:
            return None
        if normalized in {"1", "true", "t", "yes", "y"}:
            return True
        if normalized in {"0", "false", "f", "no", "n"}:
            return False
    return bool(value)


# Simple in-memory cache using Python's built-in LRU cache
@lru_cache(maxsize=1000)
def was_user_blocked_after(code: str, family: str, username: str, year: int) -> bool:
    """
    Check if user was blocked after a specific year.
    Uses @lru_cache for automatic caching.

    Timestamp precision is reduced to year to improve cache hit rate,
    since exact accuracy isn't required for this check.

    Args:
        code: Wiki code (e.g., "fi")
        family: Wiki family (e.g., "wikipedia")
        username: Username to check
        year: Year to check blocks after

    Returns:
        True if user was blocked after the given year
    """
    try:
        site = pywikibot.Site(code, family)
        # Create timestamp for start of year
        timestamp = pywikibot.Timestamp(year, 1, 1, 0, 0, 0)

        # Get block events after the timestamp
        # reverse=True means enumerate forward from start timestamp
        block_events = site.logevents(
            logtype="block",
            page=f"User:{username}",
            start=timestamp,
            reverse=True,
            total=1,  # Only need to find one block event
        )

        # Check if any 'block' action exists
        for event in block_events:
            if event.action() == "block":
                return True

        return False

    except Exception as e:
        logger.error(f"Error checking blocks for {username}: {e}")
        # Fail safe: assume NOT blocked if we can't verify
        # This prevents breaking existing functionality when the API is unavailable
        return False
