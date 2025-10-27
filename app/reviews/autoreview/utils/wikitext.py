from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reviews.models import PendingRevision

logger = logging.getLogger(__name__)


def normalize_wikitext(text: str) -> str:
    """Normalize wikitext for similarity comparison."""
    if not text:
        return ""

    # TODO: check why text is not always suitable for re.
    text = str(text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<ref[^>]*/>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"\[\[Category:[^\]]+\]\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[\[(File|Image):[^\]]+\]\]", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"'{2,}", "", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_additions(parent_wikitext: str, pending_wikitext: str) -> list[str]:
    """Extract text additions from parent to pending revision."""
    if not pending_wikitext:
        return []

    if not parent_wikitext:
        return [pending_wikitext]

    matcher = SequenceMatcher(None, parent_wikitext, pending_wikitext)
    additions = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            added_text = pending_wikitext[j1:j2]
            if added_text.strip():
                additions.append(added_text)

    return additions


def get_parent_wikitext(revision: PendingRevision) -> str:
    """Get parent revision wikitext from local database."""
    cached_parent = getattr(revision, "parent_wikitext", None)
    if isinstance(cached_parent, str) and cached_parent:
        return cached_parent

    parentid = getattr(revision, "parentid", None)
    if not isinstance(parentid, (int, str)) or not parentid:
        return ""

    try:
        from reviews.models import PendingRevision as PR

        parent_revision = PR.objects.get(page=revision.page, revid=parentid)
        return parent_revision.get_wikitext()
    except Exception:
        logger.warning(
            "Parent revision %s not found in local database for revision %s",
            revision.parentid,
            revision.revid,
        )
        return ""


def extract_references(text: str) -> list[str]:
    """Extract all reference tags from wikitext."""
    if not text:
        return []

    references = []
    ref_pattern = r"<ref[^/>]*>.*?</ref>"
    references.extend(re.findall(ref_pattern, text, flags=re.DOTALL | re.IGNORECASE))
    self_closing_pattern = r"<ref[^>]*/>"
    references.extend(re.findall(self_closing_pattern, text, flags=re.IGNORECASE))

    return references


def strip_references(text: str) -> str:
    """Remove all reference tags from wikitext."""
    if not text:
        return ""

    text = re.sub(r"<ref[^/>]*>.*?</ref>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<ref[^>]*/>\s*", "", text, flags=re.IGNORECASE)

    return text


def is_reference_only_edit(parent_wikitext: str, pending_wikitext: str) -> bool:
    """Check if edit only modifies references without changing other content."""
    if not pending_wikitext:
        return False

    parent_without_refs = strip_references(parent_wikitext or "")
    pending_without_refs = strip_references(pending_wikitext)

    parent_normalized = re.sub(r"\s+", " ", parent_without_refs).strip()
    pending_normalized = re.sub(r"\s+", " ", pending_without_refs).strip()

    if parent_normalized != pending_normalized:
        return False

    parent_refs = extract_references(parent_wikitext or "")
    pending_refs = extract_references(pending_wikitext)

    if parent_refs and not pending_refs:
        return False

    if not parent_refs and not pending_refs:
        return False

    return True


def extract_urls_from_references(references: list[str]) -> list[str]:
    """Extract all URLs from reference tags."""
    urls = []
    url_pattern = r"https?://[^\s<>\"\'\]\|]+"

    for ref in references:
        found_urls = re.findall(url_pattern, ref, flags=re.IGNORECASE)
        urls.extend(found_urls)

    return urls


def extract_domain_from_url(url: str) -> str | None:
    """Extract domain from URL without protocol, path, or query string."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        return domain if domain else None
    except Exception:
        logger.warning("Failed to parse URL: %s", url)
        return None
