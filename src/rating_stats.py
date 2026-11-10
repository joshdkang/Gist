"""Read-side rating summaries: how many reviews have a rating, the average, and the distribution.

Each import declares its own rating scale, so an entity whose files used different scales gets one
summary per scale instead of an average that mixes 1-5 stars with 0-10 scores. Ratings never feed
the analysis (see CLAUDE.md); this is only shown alongside the results.
"""
import json
import math
from collections import defaultdict
from decimal import Decimal
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ingestion import BinaryScale, LabelScale, NumericScale, RatingScale
from models import Import, Review
from schemas import RatingBucket, RatingScaleSummary, RatingSummary

# A numeric scale with whole-number ends and at most this many levels gets one bar per level
# (1-5 stars, 0-10 scores). A wider scale, or one with fractional ends, is split into
# NUMERIC_BINS equal ranges, so 1-100 doesn't get a hundred bars.
MAX_LEVELS = 11
NUMERIC_BINS = 5

scale_adapter: TypeAdapter[NumericScale | BinaryScale | LabelScale] = TypeAdapter(RatingScale)


def format_number(value: float) -> str:
    return f"{value:g}"


def numeric_buckets(scale: NumericScale, counts: dict[Decimal, int]) -> list[RatingBucket]:
    """Lowest first. A fractional rating (4.5) counts under the whole level below it."""
    low, high = scale.min, scale.max
    if low.is_integer() and high.is_integer() and high - low + 1 <= MAX_LEVELS:
        levels = list(range(int(low), int(high) + 1))
        tallies = dict.fromkeys(levels, 0)
        for rating, count in counts.items():
            tallies[min(max(math.floor(rating), levels[0]), levels[-1])] += count
        return [RatingBucket(label=str(level), count=tallies[level]) for level in levels]

    width = (high - low) / NUMERIC_BINS
    tallies = [0] * NUMERIC_BINS
    for rating, count in counts.items():
        index = int((float(rating) - low) / width) if width else 0
        tallies[min(max(index, 0), NUMERIC_BINS - 1)] += count
    return [
        RatingBucket(
            label=f"{format_number(low + width * index)}–{format_number(low + width * (index + 1))}",
            count=tallies[index],
        )
        for index in range(NUMERIC_BINS)
    ]


def summarize_scale(
    scale: NumericScale | BinaryScale | LabelScale, counts: dict[Decimal, int]
) -> RatingScaleSummary:
    """The count, average and distribution of the ratings given on one scale.

    `counts` maps each stored rating to the number of reviews with it. Stored values are the number
    itself for a numeric scale, 1 or 0 for binary, and the label's index for labels.
    """
    match scale:
        case NumericScale():
            buckets = numeric_buckets(scale, counts)
        case BinaryScale():
            buckets = [
                RatingBucket(label="Negative", count=counts.get(Decimal(0), 0)),
                RatingBucket(label="Positive", count=counts.get(Decimal(1), 0)),
            ]
        case LabelScale():
            buckets = [
                RatingBucket(label=label, count=counts.get(Decimal(index), 0))
                for index, label in enumerate(scale.labels)
            ]
    total = sum(counts.values())
    average = float(sum(rating * count for rating, count in counts.items()) / total) if total else 0.0
    return RatingScaleSummary(
        scale=scale.model_dump(), count=total, average=average, buckets=list(reversed(buckets))
    )


async def rating_summary(db: AsyncSession, entity_id: int) -> RatingSummary:
    """Every rating scale used by the entity's imports, most ratings first, in one query."""
    rows = await db.execute(
        select(Import.rating_scale, Review.rating, func.count())
        .join(Import, Review.import_id == Import.id)
        .where(Review.entity_id == entity_id)
        .group_by(Import.rating_scale, Review.rating)
    )
    total_reviews = 0
    by_scale: dict[str, dict[Decimal, int]] = defaultdict(dict)
    scales: dict[str, Any] = {}
    for raw_scale, rating, count in rows:
        total_reviews += count
        if raw_scale is None or rating is None:
            continue
        try:
            scale = scale_adapter.validate_python(raw_scale)
        except ValidationError:
            continue                    # an import with an unreadable scale has no usable ratings
        key = json.dumps(scale.model_dump(), sort_keys=True)
        scales[key] = scale
        by_scale[key][rating] = by_scale[key].get(rating, 0) + count

    summaries = [summarize_scale(scales[key], counts) for key, counts in by_scale.items()]
    summaries.sort(key=lambda summary: -summary.count)
    return RatingSummary(
        total_reviews=total_reviews,
        rated_reviews=sum(summary.count for summary in summaries),
        scales=summaries,
    )
