from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

from django.test import TestCase, override_settings

from reviews import autoreview
from reviews.autoreview import (
    _blocking_category_hits,
    _check_ores_scores,
    _find_invalid_isbns,
    _is_article_to_redirect_conversion,
    _is_bot_user,
    _is_redirect,
    _matched_user_groups,
    _normalize_to_lookup,
    _validate_isbn_10,
    _validate_isbn_13,
    is_bot_edit,
)
from reviews.services import was_user_blocked_after


class ISBNValidationTests(TestCase):
    """Test ISBN-10 and ISBN-13 checksum validation."""

    def test_valid_isbn_10_with_numeric_check_digit(self):
        """Valid ISBN-10 with numeric check digit should pass."""
        self.assertTrue(_validate_isbn_10("0306406152"))

    def test_valid_isbn_10_with_x_check_digit(self):
        """Valid ISBN-10 with 'X' check digit should pass."""
        self.assertTrue(_validate_isbn_10("043942089X"))
        self.assertTrue(_validate_isbn_10("043942089x"))  # lowercase x

    def test_invalid_isbn_10_wrong_checksum(self):
        """ISBN-10 with wrong checksum should fail."""
        self.assertFalse(_validate_isbn_10("0306406153"))  # Last digit wrong

    def test_invalid_isbn_10_too_short(self):
        """ISBN-10 with fewer than 10 digits should fail."""
        self.assertFalse(_validate_isbn_10("030640615"))

    def test_invalid_isbn_10_too_long(self):
        """ISBN-10 with more than 10 digits should fail."""
        self.assertFalse(_validate_isbn_10("03064061521"))

    def test_invalid_isbn_10_with_letters(self):
        """ISBN-10 with invalid characters should fail."""
        self.assertFalse(_validate_isbn_10("030640A152"))

    def test_valid_isbn_13_starting_with_978(self):
        """Valid ISBN-13 starting with 978 should pass."""
        self.assertTrue(_validate_isbn_13("9780306406157"))

    def test_valid_isbn_13_starting_with_979(self):
        """Valid ISBN-13 starting with 979 should pass."""
        self.assertTrue(_validate_isbn_13("9791234567896"))

    def test_invalid_isbn_13_wrong_checksum(self):
        """ISBN-13 with wrong checksum should fail."""
        self.assertFalse(_validate_isbn_13("9780306406158"))  # Last digit wrong

    def test_invalid_isbn_13_wrong_prefix(self):
        """ISBN-13 not starting with 978 or 979 should fail."""
        self.assertFalse(_validate_isbn_13("9771234567890"))

    def test_invalid_isbn_13_too_short(self):
        """ISBN-13 with fewer than 13 digits should fail."""
        self.assertFalse(_validate_isbn_13("978030640615"))

    def test_invalid_isbn_13_too_long(self):
        """ISBN-13 with more than 13 digits should fail."""
        self.assertFalse(_validate_isbn_13("97803064061571"))

    def test_invalid_isbn_13_with_letters(self):
        """ISBN-13 with non-digit characters should fail."""
        self.assertFalse(_validate_isbn_13("978030640615X"))


class ISBNDetectionTests(TestCase):
    """Test ISBN detection in wikitext."""

    def test_no_isbns_in_text(self):
        """Text without ISBNs should return empty list."""
        text = "This is just normal text without any ISBNs."
        self.assertEqual(_find_invalid_isbns(text), [])

    def test_valid_isbn_10_with_hyphens(self):
        """Valid ISBN-10 with hyphens should not be flagged."""
        text = "isbn: 0-306-40615-2"
        self.assertEqual(_find_invalid_isbns(text), [])

    def test_valid_isbn_10_with_spaces(self):
        """Valid ISBN-10 with spaces should not be flagged."""
        text = "isbn 0 306 40615 2"
        self.assertEqual(_find_invalid_isbns(text), [])

    def test_valid_isbn_10_no_separators(self):
        """Valid ISBN-10 without separators should not be flagged."""
        text = "ISBN:0306406152"
        self.assertEqual(_find_invalid_isbns(text), [])

    def test_valid_isbn_13_various_formats(self):
        """Valid ISBN-13 in various formats should not be flagged."""
        text1 = "ISBN: 978-0-306-40615-7"
        text2 = "isbn = 978 0 306 40615 7"
        text3 = "Isbn:9780306406157"
        self.assertEqual(_find_invalid_isbns(text1), [])
        self.assertEqual(_find_invalid_isbns(text2), [])
        self.assertEqual(_find_invalid_isbns(text3), [])

    def test_invalid_isbn_10_detected(self):
        """Invalid ISBN-10 should be detected."""
        text = "isbn: 0-306-40615-3"  # Wrong check digit
        invalid = _find_invalid_isbns(text)
        self.assertEqual(len(invalid), 1)
        self.assertIn("0-306-40615-3", invalid[0])

    def test_invalid_isbn_13_detected(self):
        """Invalid ISBN-13 should be detected."""
        text = "ISBN: 978-0-306-40615-8"  # Wrong check digit
        invalid = _find_invalid_isbns(text)
        self.assertEqual(len(invalid), 1)

    def test_isbn_too_short_detected(self):
        """ISBN with fewer than 10 digits should be detected as invalid."""
        text = "isbn: 123-456"
        invalid = _find_invalid_isbns(text)
        self.assertEqual(len(invalid), 1)

    def test_isbn_too_long_detected(self):
        """ISBN with more than 13 digits should be detected as invalid."""
        text = "isbn: 12345678901234"
        invalid = _find_invalid_isbns(text)
        self.assertEqual(len(invalid), 1)

    def test_multiple_valid_isbns(self):
        """Multiple valid ISBNs should not be flagged."""
        text = """
        First book: ISBN: 0-306-40615-2
        Second book: ISBN: 978-0-306-40615-7
        """
        self.assertEqual(_find_invalid_isbns(text), [])

    def test_multiple_isbns_with_one_invalid(self):
        """Text with one invalid ISBN among valid ones should flag the invalid one."""
        text = """
        Valid: ISBN: 0-306-40615-2
        Invalid: ISBN: 978-0-306-40615-8
        """
        invalid = _find_invalid_isbns(text)
        self.assertEqual(len(invalid), 1)

    def test_multiple_invalid_isbns(self):
        """Text with multiple invalid ISBNs should flag all of them."""
        text = """
        Invalid 1: ISBN: 0-306-40615-3
        Invalid 2: ISBN: 978-0-306-40615-8
        """
        invalid = _find_invalid_isbns(text)
        self.assertEqual(len(invalid), 2)

    def test_case_insensitive_isbn_detection(self):
        """ISBN detection should be case-insensitive."""
        text1 = "ISBN: 0-306-40615-2"
        text2 = "isbn: 0-306-40615-2"
        text3 = "Isbn: 0-306-40615-2"
        self.assertEqual(_find_invalid_isbns(text1), [])
        self.assertEqual(_find_invalid_isbns(text2), [])
        self.assertEqual(_find_invalid_isbns(text3), [])

    def test_isbn_with_equals_sign(self):
        """ISBN with = separator should be detected."""
        text = "isbn = 0-306-40615-2"
        self.assertEqual(_find_invalid_isbns(text), [])

    def test_isbn_with_colon(self):
        """ISBN with : separator should be detected."""
        text = "isbn: 0-306-40615-2"
        self.assertEqual(_find_invalid_isbns(text), [])

    def test_isbn_no_separator(self):
        """ISBN without separator should be detected."""
        text = "isbn 0-306-40615-2"
        self.assertEqual(_find_invalid_isbns(text), [])

    def test_real_world_wikipedia_citation(self):
        """Test with realistic Wikipedia citation format."""
        text = """
        {{cite book last=Smith first=John title=Example Book
        publisher=Example Press year=2020 isbn=978-0-306-40615-7}}
        """
        self.assertEqual(_find_invalid_isbns(text), [])

    def test_invalid_isbn_in_wikipedia_citation(self):
        """Test invalid ISBN in Wikipedia citation format."""
        text = """
        {{cite book last=Smith first=John title=Fake Book
        publisher=Fake Press year=2020 isbn=978-0-306-40615-8}}
        """
        invalid = _find_invalid_isbns(text)
        self.assertEqual(len(invalid), 1)

    def test_isbn_with_trailing_year(self):
        """Test that trailing years are not captured as part of ISBN."""
        text = "isbn: 978 0 306 40615 7 2020"
        invalid = _find_invalid_isbns(text)
        # Should recognize valid ISBN and not capture the year
        self.assertEqual(len(invalid), 0)

    def test_isbn_with_spaces_around_hyphens(self):
        """Test that ISBNs with spaces around hyphens are fully captured."""
        text = "isbn: 978 - 0 - 306 - 40615 - 7"
        invalid = _find_invalid_isbns(text)
        # Should recognize valid ISBN with spaces around hyphens
        self.assertEqual(len(invalid), 0)

    def test_isbn_followed_by_punctuation(self):
        """Test that ISBNs followed by punctuation are correctly detected."""
        # ISBN followed by comma
        text1 = "isbn: 9780306406157, 2020"
        self.assertEqual(_find_invalid_isbns(text1), [])

        # ISBN followed by period
        text2 = "isbn: 0-306-40615-2."
        self.assertEqual(_find_invalid_isbns(text2), [])

        # ISBN followed by semicolon
        text3 = "isbn: 978-0-306-40615-7; another book"
        self.assertEqual(_find_invalid_isbns(text3), [])

        # Invalid ISBN followed by comma
        text4 = "isbn: 9780306406158, 2020"
        invalid = _find_invalid_isbns(text4)
        self.assertEqual(len(invalid), 1)


@patch("reviews.autoreview.logger")
class AutoreviewBlockedUserTests(TestCase):
    def setUp(self):
        """Clear the LRU cache before each test."""
        was_user_blocked_after.cache_clear()

    @patch("reviews.services.pywikibot.Site")
    @patch("reviews.autoreview._is_bot_user")
    def test_blocked_user_not_auto_approved(self, mock_is_bot, mock_site, mock_logger):
        """Test that a user blocked after making an edit is NOT auto-approved."""
        mock_is_bot.return_value = False  # User is NOT a bot

        # Mock the pywikibot.Site and logevents to return a block event
        mock_site_instance = MagicMock()
        mock_site.return_value = mock_site_instance

        # Create a mock block event
        mock_block_event = MagicMock()
        mock_block_event.action.return_value = "block"
        mock_site_instance.logevents.return_value = [mock_block_event]

        profile = MagicMock()
        profile.usergroups = []
        profile.is_bot = False

        mock_wiki = MagicMock()
        mock_wiki.code = "fi"
        mock_wiki.family = "wikipedia"

        revision = MagicMock()
        revision.user_name = "BlockedUser"
        revision.timestamp = datetime.fromisoformat("2024-01-15T10:00:00")
        revision.page.categories = []
        revision.page.wiki = mock_wiki

        # Create a mock WikiClient - but we need the real is_user_blocked_after_edit method
        from reviews.services import WikiClient

        mock_client = WikiClient(mock_wiki)

        # Call with correct signature: revision, client, profile, **kwargs
        result = autoreview._evaluate_revision(
            revision,
            mock_client,
            profile,
            auto_groups={},
            blocking_categories={},
            redirect_aliases=[],
        )

        # Assert
        self.assertEqual(result["decision"].status, "blocked")
        self.assertTrue(any(t["id"] == "blocked-user" for t in result["tests"]))

        # Verify pywikibot.Site was called (will be called twice:
        # once in WikiClient.__init__, once in was_user_blocked_after)
        self.assertGreaterEqual(mock_site.call_count, 1)

        # Verify logevents was called with correct parameters
        mock_site_instance.logevents.assert_called_once()

        # Verify no error logs were called
        mock_logger.error.assert_not_called()


@patch("reviews.autoreview.logger")
class OresScoreTests(TestCase):
    """Test ORES damaging and goodfaith score checks."""

    @patch("reviews.autoreview.http.fetch")
    def test_ores_damaging_score_exceeds_threshold(self, mock_fetch, mock_logger):
        """Test that high damaging score blocks auto-approval."""
        # Mock ORES API response with high damaging score
        mock_response = Mock()
        mock_response.headers = {}
        mock_response.text = json.dumps(
            {
                "fiwiki": {
                    "scores": {
                        "12345": {
                            "damaging": {
                                "score": {
                                    "prediction": True,
                                    "probability": {"true": 0.85, "false": 0.15},
                                }
                            }
                        }
                    }
                }
            }
        )
        mock_fetch.return_value = mock_response

        # Create mock revision
        mock_revision = MagicMock()
        mock_revision.revid = 12345
        mock_revision.page.wiki.code = "fi"
        mock_revision.page.wiki.family = "wikipedia"

        # Check with threshold of 0.7
        result = _check_ores_scores(mock_revision, damaging_threshold=0.7, goodfaith_threshold=0.0)

        self.assertTrue(result["should_block"])
        self.assertEqual(result["test"]["status"], "fail")
        self.assertIn("0.850", result["test"]["message"])

        # Verify no logs were called
        mock_logger.error.assert_not_called()

    @patch("reviews.autoreview.http.fetch")
    def test_ores_goodfaith_score_below_threshold(self, mock_fetch, mock_logger):
        """Test that low goodfaith score blocks auto-approval."""
        # Mock ORES API response with low goodfaith score
        mock_response = Mock()
        mock_response.headers = {}
        mock_response.text = json.dumps(
            {
                "fiwiki": {
                    "scores": {
                        "12345": {
                            "goodfaith": {
                                "score": {
                                    "prediction": False,
                                    "probability": {"true": 0.3, "false": 0.7},
                                }
                            }
                        }
                    }
                }
            }
        )
        mock_fetch.return_value = mock_response

        # Create mock revision
        mock_revision = MagicMock()
        mock_revision.revid = 12345
        mock_revision.page.wiki.code = "fi"
        mock_revision.page.wiki.family = "wikipedia"

        # Check with threshold of 0.5
        result = _check_ores_scores(mock_revision, damaging_threshold=0.0, goodfaith_threshold=0.5)

        self.assertTrue(result["should_block"])
        self.assertEqual(result["test"]["status"], "fail")
        self.assertIn("0.300", result["test"]["message"])

        # Verify no logs were called
        mock_logger.error.assert_not_called()

    @patch("reviews.autoreview.http.fetch")
    def test_ores_scores_within_thresholds(self, mock_fetch, mock_logger):
        """Test that good scores pass the check."""
        # Mock ORES API response with good scores
        mock_response = Mock()
        mock_response.headers = {}
        mock_response.text = json.dumps(
            {
                "fiwiki": {
                    "scores": {
                        "12345": {
                            "damaging": {
                                "score": {
                                    "prediction": False,
                                    "probability": {"true": 0.02, "false": 0.98},
                                }
                            },
                            "goodfaith": {
                                "score": {
                                    "prediction": True,
                                    "probability": {"true": 0.999, "false": 0.001},
                                }
                            },
                        }
                    }
                }
            }
        )
        mock_fetch.return_value = mock_response

        # Create mock revision
        mock_revision = MagicMock()
        mock_revision.revid = 12345
        mock_revision.page.wiki.code = "fi"
        mock_revision.page.wiki.family = "wikipedia"

        # Check with reasonable thresholds
        result = _check_ores_scores(mock_revision, damaging_threshold=0.7, goodfaith_threshold=0.5)

        self.assertFalse(result["should_block"])
        self.assertEqual(result["test"]["status"], "ok")
        self.assertIn("damaging: 0.020", result["test"]["message"])
        self.assertIn("goodfaith: 0.999", result["test"]["message"])

        # Verify no logs were called
        mock_logger.error.assert_not_called()

    def test_ores_checks_disabled_when_thresholds_zero(self, mock_logger):
        """Test that ORES checks are skipped when thresholds are 0.0."""
        mock_revision = MagicMock()
        mock_revision.revid = 12345
        mock_revision.page.wiki.code = "fi"
        mock_revision.page.wiki.family = "wikipedia"

        result = _check_ores_scores(mock_revision, damaging_threshold=0.0, goodfaith_threshold=0.0)

        self.assertFalse(result["should_block"])
        self.assertEqual(result["test"]["status"], "skip")
        self.assertIn("disabled", result["test"]["message"])

        # Verify no logs were called
        mock_logger.error.assert_not_called()

    @patch("reviews.autoreview.http.fetch")
    def test_ores_api_error_blocks_approval(self, mock_fetch, mock_logger):
        """Test that ORES API errors block auto-approval (safe default)."""
        # Mock API error
        mock_fetch.side_effect = Exception("API connection failed")

        # Create mock revision
        mock_revision = MagicMock()
        mock_revision.revid = 12345
        mock_revision.page.wiki.code = "fi"
        mock_revision.page.wiki.family = "wikipedia"

        result = _check_ores_scores(mock_revision, damaging_threshold=0.7, goodfaith_threshold=0.5)

        self.assertTrue(result["should_block"])
        self.assertEqual(result["test"]["status"], "fail")
        self.assertIn("Could not verify", result["test"]["message"])

        # Verify error log was called but don't print it
        mock_logger.error.assert_called_once()

    @patch("reviews.autoreview.http.fetch")
    def test_ores_only_damaging_check_enabled(self, mock_fetch, mock_logger):
        """Test checking only damaging score when goodfaith threshold is 0."""
        mock_response = Mock()
        mock_response.headers = {}
        mock_response.text = json.dumps(
            {
                "fiwiki": {
                    "scores": {
                        "12345": {
                            "damaging": {
                                "score": {
                                    "prediction": False,
                                    "probability": {"true": 0.05, "false": 0.95},
                                }
                            }
                        }
                    }
                }
            }
        )
        mock_fetch.return_value = mock_response

        mock_revision = MagicMock()
        mock_revision.revid = 12345
        mock_revision.page.wiki.code = "fi"
        mock_revision.page.wiki.family = "wikipedia"

        result = _check_ores_scores(mock_revision, damaging_threshold=0.7, goodfaith_threshold=0.0)

        self.assertFalse(result["should_block"])
        self.assertEqual(result["test"]["status"], "ok")
        self.assertIn("damaging: 0.050", result["test"]["message"])
        self.assertNotIn("goodfaith", result["test"]["message"])

        # Verify no logs were called
        mock_logger.error.assert_not_called()

    @patch("reviews.autoreview.http.fetch")
    def test_ores_only_goodfaith_check_enabled(self, mock_fetch, mock_logger):
        """Test checking only goodfaith score when damaging threshold is 0."""
        mock_response = Mock()
        mock_response.headers = {}
        mock_response.text = json.dumps(
            {
                "fiwiki": {
                    "scores": {
                        "12345": {
                            "goodfaith": {
                                "score": {
                                    "prediction": True,
                                    "probability": {"true": 0.95, "false": 0.05},
                                }
                            }
                        }
                    }
                }
            }
        )
        mock_fetch.return_value = mock_response

        mock_revision = MagicMock()
        mock_revision.revid = 12345
        mock_revision.page.wiki.code = "fi"
        mock_revision.page.wiki.family = "wikipedia"

        result = _check_ores_scores(mock_revision, damaging_threshold=0.0, goodfaith_threshold=0.5)

        self.assertFalse(result["should_block"])
        self.assertEqual(result["test"]["status"], "ok")
        self.assertIn("goodfaith: 0.950", result["test"]["message"])
        self.assertNotIn("damaging", result["test"]["message"])

        # Verify no logs were called
        mock_logger.error.assert_not_called()

    @override_settings(ORES_DAMAGING_THRESHOLD=0.7, ORES_GOODFAITH_THRESHOLD=0.5)
    @patch("reviews.services.pywikibot.Site")
    @patch("reviews.autoreview.http.fetch")
    @patch("reviews.autoreview._is_bot_user")
    @patch("reviews.autoreview.is_living_person")
    def test_ores_integration_in_evaluate_revision(
        self, mock_is_living, mock_is_bot, mock_fetch, mock_site, mock_logger
    ):
        """Test ORES check integration in _evaluate_revision."""
        mock_is_bot.return_value = False
        mock_is_living.return_value = False  # Mock to prevent pywikibot calls

        # Mock pywikibot.Site for WikiClient
        mock_site_instance = MagicMock()
        mock_site.return_value = mock_site_instance
        mock_site_instance.logevents.return_value = []

        # Mock ORES API response with high damaging score
        mock_response = Mock()
        mock_response.headers = {}
        mock_response.text = json.dumps(
            {
                "fiwiki": {
                    "scores": {
                        "12345": {
                            "damaging": {
                                "score": {
                                    "prediction": True,
                                    "probability": {"true": 0.85, "false": 0.15},
                                }
                            },
                            "goodfaith": {
                                "score": {
                                    "prediction": False,
                                    "probability": {"true": 0.2, "false": 0.8},
                                }
                            },
                        }
                    }
                }
            }
        )
        mock_fetch.return_value = mock_response

        # Create mock objects
        mock_wiki = MagicMock()
        mock_wiki.code = "fi"
        mock_wiki.family = "wikipedia"
        mock_wiki.configuration.ores_damaging_threshold = 0.7
        mock_wiki.configuration.ores_goodfaith_threshold = 0.5
        mock_wiki.configuration.ores_damaging_threshold_living = 0.1
        mock_wiki.configuration.ores_goodfaith_threshold_living = 0.9

        mock_page = MagicMock()
        mock_page.wiki = mock_wiki
        mock_page.title = "Test Page"
        mock_page.categories = []

        mock_revision = MagicMock()
        mock_revision.revid = 12345
        mock_revision.page = mock_page
        mock_revision.user_name = "TestUser"
        mock_revision.timestamp = datetime.fromisoformat("2024-01-15T10:00:00")
        mock_revision.get_wikitext.return_value = "Test content"

        from reviews.services import WikiClient

        mock_client = WikiClient(mock_wiki)

        # Call _evaluate_revision
        result = autoreview._evaluate_revision(
            mock_revision,
            mock_client,
            None,
            auto_groups={},
            blocking_categories={},
            redirect_aliases=[],
        )

        # Should be blocked due to high damaging score
        self.assertEqual(result["decision"].status, "blocked")
        self.assertTrue(any(t["id"] == "ores-scores" for t in result["tests"]))

        # Verify no error logs (but warning might be called for pywikibot page access)
        mock_logger.error.assert_not_called()


class RedirectDetectionTests(TestCase):
    """Test redirect detection and article-to-redirect conversion."""

    def test_is_redirect_with_english_alias(self):
        """Test redirect detection with English #REDIRECT."""
        wikitext = "#REDIRECT [[Target Page]]"
        aliases = ["#REDIRECT"]
        self.assertTrue(_is_redirect(wikitext, aliases))

    def test_is_redirect_case_insensitive(self):
        """Test redirect detection is case-insensitive."""
        wikitext = "#redirect [[Target Page]]"
        aliases = ["#REDIRECT"]
        self.assertTrue(_is_redirect(wikitext, aliases))

    def test_is_redirect_with_spaces(self):
        """Test redirect detection with spaces after #."""
        wikitext = "#  REDIRECT [[Target Page]]"
        aliases = ["#REDIRECT"]
        self.assertTrue(_is_redirect(wikitext, aliases))

    def test_is_redirect_with_finnish_alias(self):
        """Test redirect detection with Finnish #OHJAUS."""
        wikitext = "#OHJAUS [[Kohde sivu]]"
        aliases = ["#OHJAUS", "#REDIRECT"]
        self.assertTrue(_is_redirect(wikitext, aliases))

    def test_is_not_redirect_regular_content(self):
        """Test that regular content is not detected as redirect."""
        wikitext = "This is a normal article with #REDIRECT mentioned in text."
        aliases = ["#REDIRECT"]
        self.assertFalse(_is_redirect(wikitext, aliases))

    def test_is_not_redirect_empty_wikitext(self):
        """Test that empty wikitext is not a redirect."""
        self.assertFalse(_is_redirect("", ["#REDIRECT"]))
        self.assertFalse(_is_redirect(None, ["#REDIRECT"]))

    def test_is_not_redirect_no_aliases(self):
        """Test that no aliases means no redirect detection."""
        wikitext = "#REDIRECT [[Target]]"
        self.assertFalse(_is_redirect(wikitext, []))
        self.assertFalse(_is_redirect(wikitext, None))

    @patch("reviews.autoreview._get_parent_wikitext")
    def test_article_to_redirect_conversion_detected(self, mock_get_parent):
        """Test article-to-redirect conversion is detected."""
        mock_revision = MagicMock()
        mock_revision.get_wikitext.return_value = "#REDIRECT [[Target]]"
        mock_revision.parentid = 123

        mock_get_parent.return_value = "This is article content"

        aliases = ["#REDIRECT"]
        result = _is_article_to_redirect_conversion(mock_revision, aliases)

        self.assertTrue(result)

    @patch("reviews.autoreview._get_parent_wikitext")
    def test_redirect_to_redirect_not_conversion(self, mock_get_parent):
        """Test redirect-to-redirect is not a conversion."""
        mock_revision = MagicMock()
        mock_revision.get_wikitext.return_value = "#REDIRECT [[New Target]]"
        mock_revision.parentid = 123

        mock_get_parent.return_value = "#REDIRECT [[Old Target]]"

        aliases = ["#REDIRECT"]
        result = _is_article_to_redirect_conversion(mock_revision, aliases)

        self.assertFalse(result)

    def test_article_to_redirect_no_parent(self):
        """Test no conversion when there's no parent."""
        mock_revision = MagicMock()
        mock_revision.get_wikitext.return_value = "#REDIRECT [[Target]]"
        mock_revision.parentid = None

        aliases = ["#REDIRECT"]
        result = _is_article_to_redirect_conversion(mock_revision, aliases)

        self.assertFalse(result)


class HelperFunctionsTests(TestCase):
    """Test helper functions for normalization and matching."""

    def test_normalize_to_lookup(self):
        """Test normalization creates case-insensitive lookup."""
        values = ["Admin", "Sysop", "BUREAUCRAT"]
        result = _normalize_to_lookup(values)

        self.assertEqual(result["admin"], "Admin")
        self.assertEqual(result["sysop"], "Sysop")
        self.assertEqual(result["bureaucrat"], "BUREAUCRAT")

    def test_normalize_to_lookup_empty(self):
        """Test normalization with empty input."""
        self.assertEqual(_normalize_to_lookup(None), {})
        self.assertEqual(_normalize_to_lookup([]), {})

    def test_normalize_to_lookup_filters_empty_strings(self):
        """Test normalization filters out empty strings."""
        values = ["Admin", "", "Sysop", None]
        result = _normalize_to_lookup(values)

        self.assertEqual(len(result), 2)
        self.assertIn("admin", result)
        self.assertIn("sysop", result)

    def test_matched_user_groups_from_profile(self):
        """Test matching user groups from profile."""
        mock_revision = MagicMock()
        mock_revision.superset_data = {}

        mock_profile = MagicMock()
        mock_profile.usergroups = ["sysop", "autoreviewer"]

        allowed_groups = _normalize_to_lookup(["sysop", "admin"])

        result = _matched_user_groups(mock_revision, mock_profile, allowed_groups=allowed_groups)

        self.assertEqual(result, {"sysop"})

    def test_matched_user_groups_from_superset(self):
        """Test matching user groups from superset data."""
        mock_revision = MagicMock()
        mock_revision.superset_data = {"user_groups": ["admin", "bot"]}

        allowed_groups = _normalize_to_lookup(["admin", "bureaucrat"])

        result = _matched_user_groups(mock_revision, None, allowed_groups=allowed_groups)

        self.assertEqual(result, {"admin"})

    def test_matched_user_groups_case_insensitive(self):
        """Test user group matching is case-insensitive."""
        mock_revision = MagicMock()
        mock_revision.superset_data = {"user_groups": ["SYSOP"]}

        mock_profile = MagicMock()
        mock_profile.usergroups = []

        allowed_groups = _normalize_to_lookup(["sysop"])

        result = _matched_user_groups(mock_revision, mock_profile, allowed_groups=allowed_groups)

        self.assertEqual(result, {"sysop"})

    def test_blocking_category_hits(self):
        """Test blocking category detection."""
        mock_revision = MagicMock()
        mock_revision.get_categories.return_value = ["Living people", "American politicians"]
        mock_revision.page.categories = []

        blocking_lookup = _normalize_to_lookup(["Living people", "BLP"])

        result = _blocking_category_hits(mock_revision, blocking_lookup)

        self.assertEqual(result, {"Living people"})

    def test_blocking_category_hits_from_page(self):
        """Test blocking category detection from page.categories."""
        mock_revision = MagicMock()
        mock_revision.get_categories.return_value = []
        mock_revision.page.categories = ["Living people"]

        blocking_lookup = _normalize_to_lookup(["Living people"])

        result = _blocking_category_hits(mock_revision, blocking_lookup)

        self.assertEqual(result, {"Living people"})


class BotDetectionTests(TestCase):
    """Test bot user detection."""

    def test_is_bot_user_from_superset_rc_bot(self):
        """Test bot detection from superset rc_bot flag."""
        mock_revision = MagicMock()
        mock_revision.superset_data = {"rc_bot": True}

        result = _is_bot_user(mock_revision, None)

        self.assertTrue(result)

    @patch("reviews.autoreview.is_bot_edit")
    def test_is_bot_user_from_profile(self, mock_is_bot_edit):
        """Test bot detection from profile via is_bot_edit."""
        mock_revision = MagicMock()
        mock_revision.superset_data = {}

        mock_is_bot_edit.return_value = True

        result = _is_bot_user(mock_revision, None)

        self.assertTrue(result)

    def test_is_bot_user_not_bot(self):
        """Test non-bot user detection."""
        mock_revision = MagicMock()
        mock_revision.superset_data = {}

        with patch("reviews.autoreview.is_bot_edit", return_value=False):
            result = _is_bot_user(mock_revision, None)

        self.assertFalse(result)

    def test_is_bot_edit_with_bot_profile(self):
        """Test is_bot_edit with bot profile."""
        from reviews.models import EditorProfile, PendingPage, Wiki

        # Create test data
        wiki = Wiki.objects.create(code="en", family="wikipedia")
        EditorProfile.objects.create(
            wiki=wiki, username="BotUser", is_bot=True, is_autopatrolled=False
        )

        PendingPage.objects.create(wiki=wiki, pageid=123, title="Test Page", stable_revid=100)

        mock_revision = MagicMock()
        mock_revision.user_name = "BotUser"
        mock_revision.page.wiki = wiki

        result = is_bot_edit(mock_revision)

        self.assertTrue(result)

    def test_is_bot_edit_with_former_bot_profile(self):
        """Test is_bot_edit with former bot profile."""
        from reviews.models import EditorProfile, Wiki

        # Create test data
        wiki = Wiki.objects.create(code="de", family="wikipedia")
        EditorProfile.objects.create(
            wiki=wiki,
            username="FormerBot",
            is_bot=False,
            is_former_bot=True,
            is_autopatrolled=False,
        )

        mock_revision = MagicMock()
        mock_revision.user_name = "FormerBot"
        mock_revision.page.wiki = wiki

        result = is_bot_edit(mock_revision)

        self.assertTrue(result)

    def test_is_bot_edit_no_username(self):
        """Test is_bot_edit with no username."""
        mock_revision = MagicMock()
        mock_revision.user_name = None

        result = is_bot_edit(mock_revision)

        self.assertFalse(result)

    def test_is_bot_edit_profile_not_exists(self):
        """Test is_bot_edit when profile doesn't exist."""
        from reviews.models import Wiki

        wiki = Wiki.objects.create(code="fr", family="wikipedia")

        mock_revision = MagicMock()
        mock_revision.user_name = "NonExistentUser"
        mock_revision.page.wiki = wiki

        result = is_bot_edit(mock_revision)

        self.assertFalse(result)
