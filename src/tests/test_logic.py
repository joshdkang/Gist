"""Pure functions: ingestion parsing and hashing, theme names and titles, sentiment, thresholds."""
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from classify_reviews import is_valid_title, normalize_title
from consolidate_themes import promote_min_reviews
from discover_themes import check_theme_names
from ingestion import (
    BinaryScale,
    LabelScale,
    NumericScale,
    compute_content_hash,
    normalize_text,
    parse_date,
    parse_rating,
)
from models import Sentiment
from theme_stats import combine_sentiments


# --- ingestion -------------------------------------------------------------------------------

def test_content_hash_ignores_case_and_spacing_but_not_the_date():
    date = datetime(2020, 1, 2, tzinfo=UTC)
    assert compute_content_hash("Great  food\n", date) == compute_content_hash("great food", date)
    assert compute_content_hash("great food", date) != compute_content_hash("great food", None)
    assert compute_content_hash("great food", date) != compute_content_hash("great drinks", date)
    assert normalize_text("  A  B\tC ") == "a b c"


def test_parse_date_reads_iso_and_custom_formats_and_assumes_utc():
    assert parse_date("2017-01-13T00:00:00.000Z", None) == datetime(2017, 1, 13, tzinfo=UTC)
    assert parse_date("2017-01-13", None) == datetime(2017, 1, 13, tzinfo=UTC)
    assert parse_date("October 12 2016", "%B %d %Y") == datetime(2016, 10, 12, tzinfo=UTC)
    assert parse_date("not a date", None) is None
    assert parse_date("2017-01-13", "%B %d %Y") is None


def test_numeric_ratings_must_be_on_the_scale():
    scale = NumericScale(min=1, max=5)
    assert parse_rating("5.0", scale) == Decimal("5.0")
    assert parse_rating("1", scale) == Decimal(1)
    assert parse_rating("6", scale) is None
    assert parse_rating("0", scale) is None
    assert parse_rating("great", scale) is None


def test_binary_and_label_ratings():
    binary = BinaryScale()
    assert parse_rating("Yes", binary) == 1 and parse_rating("no", binary) == 0
    assert parse_rating("maybe", binary) is None
    labels = LabelScale(labels=["bad", "ok", "Good"])
    assert parse_rating("good", labels) == 2 and parse_rating("BAD", labels) == 0
    assert parse_rating("great", labels) is None


# --- theme names and titles ------------------------------------------------------------------

def test_titles_are_matched_by_a_normalized_key():
    assert normalize_title("Short Ribs") == normalize_title("short  rib")
    assert normalize_title("Parking lots") == normalize_title("parking lot")
    assert normalize_title("Glass") == "glass"                  # a double s is not a plural
    assert normalize_title("Bread & butter!") == "bread & butter"


@pytest.mark.parametrize("title", ["Parking", "Battery life", "Price and value"])
def test_good_titles_are_valid(title):
    assert is_valid_title(title)


@pytest.mark.parametrize("title", ["", "Other dishes", "Miscellaneous", "General", "x" * 81])
def test_empty_residual_reserved_and_long_titles_are_discarded(title):
    assert not is_valid_title(title)


def test_discovery_rejects_repeated_reserved_and_leftover_names():
    check_theme_names(["Service speed", "Price and value"])
    with pytest.raises(ValueError, match="unique"):
        check_theme_names(["Service", "service"])
    with pytest.raises(ValueError, match="catch-all"):
        check_theme_names(["Service", "General"])
    with pytest.raises(ValueError, match="leftover"):
        check_theme_names(["Service", "Other dishes"])


# --- sentiment and thresholds ----------------------------------------------------------------

@pytest.mark.parametrize("sentiments,expected", [
    ([], None),
    ([None, None], None),
    ([Sentiment.POSITIVE], Sentiment.POSITIVE),
    ([Sentiment.POSITIVE, Sentiment.POSITIVE], Sentiment.POSITIVE),
    ([Sentiment.POSITIVE, Sentiment.NEGATIVE], Sentiment.MIXED),
    ([Sentiment.NEGATIVE, Sentiment.MIXED], Sentiment.MIXED),
    ([Sentiment.NEUTRAL, Sentiment.POSITIVE], Sentiment.POSITIVE),
    ([Sentiment.NEUTRAL, Sentiment.NEGATIVE], Sentiment.NEGATIVE),
    ([Sentiment.NEUTRAL, None], Sentiment.NEUTRAL),
])
def test_one_review_one_sentiment_per_theme(sentiments, expected):
    assert combine_sentiments(sentiments) == expected


def test_a_theme_needs_five_percent_of_reviews_and_at_least_25():
    assert promote_min_reviews(100) == 25                  # the floor
    assert promote_min_reviews(500) == 25
    assert promote_min_reviews(1000) == 50                 # 5%
    assert promote_min_reviews(4480) == 224
