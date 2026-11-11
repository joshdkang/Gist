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
from rating_stats import summarize_scale
from theme_stats import combine_sentiments, fisher_exact_p, shift_period, significant_changes


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


# --- ratings ---------------------------------------------------------------------------------

def bucket_counts(summary) -> list[tuple[str, int]]:
    return [(bucket.label, bucket.count) for bucket in summary.buckets]


def test_a_five_point_scale_has_a_bar_per_level_highest_first():
    counts = {Decimal(5): 6, Decimal(4): 2, Decimal("4.5"): 1, Decimal(1): 1}
    summary = summarize_scale(NumericScale(min=1, max=5), counts)
    assert summary.count == 10 and summary.average == pytest.approx(4.35)
    # A half rating counts under the level below it; levels with no ratings still get a bar.
    assert bucket_counts(summary) == [("5", 6), ("4", 3), ("3", 0), ("2", 0), ("1", 1)]


def test_a_wide_numeric_scale_is_split_into_five_ranges():
    counts = {Decimal(0): 1, Decimal(19): 1, Decimal(20): 1, Decimal(100): 2}
    summary = summarize_scale(NumericScale(min=0, max=100), counts)
    assert bucket_counts(summary) == [("80–100", 2), ("60–80", 0), ("40–60", 0), ("20–40", 1), ("0–20", 2)]


def test_binary_and_label_scales_summarize_their_own_way():
    binary = summarize_scale(BinaryScale(), {Decimal(1): 3, Decimal(0): 1})
    assert binary.average == 0.75 and bucket_counts(binary) == [("Positive", 3), ("Negative", 1)]
    labels = summarize_scale(LabelScale(labels=["bad", "ok", "good"]), {Decimal(2): 2, Decimal(0): 2})
    assert labels.average == 1.0 and bucket_counts(labels) == [("good", 2), ("ok", 0), ("bad", 2)]


def test_shift_period_steps_whole_calendar_periods():
    start = datetime(2022, 1, 1, tzinfo=UTC)
    assert shift_period(start, "month", -1) == datetime(2021, 12, 1, tzinfo=UTC)
    assert shift_period(start, "quarter", -1) == datetime(2021, 10, 1, tzinfo=UTC)
    assert shift_period(start, "year", -1) == datetime(2021, 1, 1, tzinfo=UTC)
    assert shift_period(datetime(2022, 11, 1, tzinfo=UTC), "quarter", 1) == datetime(2023, 2, 1, tzinfo=UTC)
    assert shift_period(datetime(2022, 1, 3, tzinfo=UTC), "week", -1) == datetime(2021, 12, 27, tzinfo=UTC)


def test_fisher_exact_p_is_exact_at_small_counts():
    assert fisher_exact_p(3, 5, 1, 5) == pytest.approx(0.5238, abs=1e-4)   # 60% to 20% of 5 each
    assert fisher_exact_p(50, 100, 70, 100) == pytest.approx(0.0059, abs=1e-4)
    assert fisher_exact_p(0, 590, 4, 538) == pytest.approx(0.0514, abs=1e-4)  # a z-test says 0.04
    assert fisher_exact_p(5, 10, 5, 10) == pytest.approx(1.0)
    assert fisher_exact_p(10, 10, 20, 20) == pytest.approx(1.0)              # all in both
    assert fisher_exact_p(0, 0, 4, 10) is None                               # an empty side


def test_significant_changes_raise_the_bar_with_the_number_of_tests():
    # Alone, 0.03 is under 5%; among four tests its rank (2) allows only 2/4 * 5% = 2.5%.
    assert significant_changes([0.03]) == [True]
    assert significant_changes([0.001, 0.03, 0.2, 0.9]) == [True, False, False, False]
    # The largest rank under its bar marks every smaller p-value too: 0.04 <= 4/4 * 5%.
    assert significant_changes([0.04, 0.03, 0.035, 0.02]) == [True, True, True, True]
    # Nothing to compare is never significant and isn't counted as a test.
    assert significant_changes([None, 0.03, None]) == [False, True, False]
    assert significant_changes([]) == []
