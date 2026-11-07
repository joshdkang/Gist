"""Confirming the themes discovery saved, once every review is classified."""
from sqlalchemy import update

from confirm_themes import confirm_provisional_themes, has_provisional_themes
from models import Theme, ThemeKind


async def test_themes_under_the_threshold_are_demoted_and_the_rest_confirmed(db, seeded):
    # 40 classified reviews: the threshold is the floor of 25. Service has 30, Price has 10.
    await db.execute(
        update(Theme).where(Theme.id.in_([seeded.service.id, seeded.price.id])).values(provisional=True)
    )
    await db.commit()
    assert await has_provisional_themes(db, seeded.entity_id)

    summary = await confirm_provisional_themes(db, seeded.entity_id)

    assert summary.threshold == 25 and summary.classified_reviews == 40
    assert summary.confirmed == ["Service"] and summary.demoted == ["Price"]
    assert "Price" in summary.describe() and "Service" not in summary.describe()
    service, price = await db.get(Theme, seeded.service.id), await db.get(Theme, seeded.price.id)
    await db.refresh(service)
    await db.refresh(price)
    assert (service.kind, service.provisional) == (ThemeKind.DISCOVERED, False)
    assert (price.kind, price.provisional) == (ThemeKind.CANDIDATE, False)
    assert price.name == "Price" and price.description == "About price."    # kept as it was
    assert not await has_provisional_themes(db, seeded.entity_id)


async def test_confirming_with_nothing_provisional_changes_nothing(db, seeded):
    summary = await confirm_provisional_themes(db, seeded.entity_id)
    assert summary.confirmed == [] and summary.demoted == [] and summary.describe() == ""
