"""Confirm the themes discovery saved, once every review has been classified.

Discovery judges its themes from a sample, and the stratified sample over-represents rare ratings,
so a theme can clear the threshold in the sample and be far under it across all the reviews. The
themes are saved as provisional (`themes.provisional`). When the first classification has finished,
each one's real share is counted from the tags, the same count promotion uses. A theme that fewer
reviews raise than promotion asks for (`promote_min_reviews`: 5% of the classified reviews, at least
25) is demoted to a candidate. It keeps its name, description and tags, and promotion can bring it
back if it grows. Every other provisional theme becomes a normal discovered theme.

It is one threshold for becoming a theme and staying one. A demoted theme is under it, so promotion
(which needs the count to reach it) never promotes the theme straight back.

Calls no model. The worker runs it as the `confirm` step; there is no separate script.
"""
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from consolidate_themes import candidate_share, promote_min_reviews
from models import ReviewTheme, Theme, ThemeKind


@dataclass
class ConfirmSummary:
    threshold: int = 0
    classified_reviews: int = 0
    confirmed: list[str] = field(default_factory=list)
    demoted: list[str] = field(default_factory=list)

    def describe(self) -> str:
        """Notes for the step: which themes were demoted and how many reviews they needed."""
        if not self.demoted:
            return ""
        return f"Demoted to candidates (under {self.threshold} of {self.classified_reviews} reviews): " + ", ".join(
            self.demoted
        ) + "."


async def has_provisional_themes(db: AsyncSession, entity_id: int) -> bool:
    return bool(
        await db.scalar(
            select(func.count()).select_from(Theme).where(Theme.entity_id == entity_id, Theme.provisional)
        )
    )


async def confirm_provisional_themes(db: AsyncSession, entity_id: int) -> ConfirmSummary:
    """Demote the provisional themes too few reviews raise, and confirm the rest. Commits."""
    _, classified_reviews = await candidate_share(db, entity_id)
    summary = ConfirmSummary(
        threshold=promote_min_reviews(classified_reviews), classified_reviews=classified_reviews
    )
    themes = list(
        await db.scalars(
            select(Theme).where(Theme.entity_id == entity_id, Theme.provisional).order_by(Theme.id)
        )
    )
    counts = dict(
        (
            await db.execute(
                select(ReviewTheme.theme_id, func.count(func.distinct(ReviewTheme.review_id)))
                .where(ReviewTheme.theme_id.in_([theme.id for theme in themes]))
                .group_by(ReviewTheme.theme_id)
            )
        ).all()
    )
    for theme in themes:
        if counts.get(theme.id, 0) < summary.threshold:
            summary.demoted.append(theme.name)
            kind = ThemeKind.CANDIDATE
        else:
            summary.confirmed.append(theme.name)
            kind = ThemeKind.DISCOVERED
        await db.execute(update(Theme).where(Theme.id == theme.id).values(kind=kind, provisional=False))
    await db.commit()
    return summary
