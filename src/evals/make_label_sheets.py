"""Choose the hand-labelled development and test sets for an entity, and write their labelling workbooks.

    python evals/make_label_sheets.py            (from src/; Cochon, entity 11)

The labels are free-form (open coding): for each review, the topics it raises in the labeller's own
words, each with a sentiment. The labeller never sees the model's themes, so the labels can judge
discovery: the topics are grouped into a codebook, and discovery runs are scored against it. Once the
themes are settled, each codebook entry maps to a theme, which turns the same labels into
classification labels without labelling again.

Two sets of 100 reviews. The development set is for tuning: half random, half four- and five-star
reviews with a neutral tag on a dish or drink theme (where the classifier was found to go wrong),
including the reviews already read while finding that problem. The test set is 100 random reviews
that exclude everything read so far; it is scored once, at the end, for the write-up, so its numbers
describe the reviews as they are and judge a prompt on reviews it was not tuned on.

The sets are written as ID lists (cochon_dev_ids.txt, cochon_test_ids.txt), which classify_reviews.py
takes with --review-ids, and as workbooks. The workbooks hold review text from the Yelp dataset, so
they stay out of git; the labels are read back from them. Rerunning picks the same reviews (the order
is seeded), and never overwrites a workbook.
"""
import asyncio
import random
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from classify_reviews import MAX_REVIEW_CHARS, has_enough_text  # noqa: E402
from database import async_session_maker, engine  # noqa: E402

ENTITY_ID = 11  # Cochon
SEED = "labels-2026-11"
HERE = Path(__file__).parent
SET_SIZE = 100
DEV_ENRICHED = 50  # of the development set's 100, the rest random
TOPIC_SLOTS = 10  # topic and sentiment pairs per review
# Cochon's dish and drink themes, where neutral tags on praised dishes were found (docs/decisions.md).
DISH_THEMES = [118, 119, 120, 121, 122, 123, 125, 1937, 2074, 2107, 2448]
# Read while finding the problem: in the development set, never in the test set.
KNOWN = {
    56618: "praise of a list of dishes, tagged neutral",
    53554: '"They were all flavorful", two of three dishes neutral',
    55853: '"Everything was flavorful and very good", rabbit neutral',
    52623: "one list, rabbit positive and cochon neutral",
    56135: 'dishes covered only by "very very good comfort food"',
    55877: "alligator joke, neutral",
    56036: 'alligator "quite interesting", lukewarm',
    53897: 'oysters "decent", lukewarm',
    53281: '"our favorite of the three was the pork cheeks"',
    53842: "reservation advice",
    54518: "reservation mentioned in passing",
    53912: "reservation advice",
}
SEEN = set(KNOWN) | {53818, 56557, 55242}  # also printed while sampling, so kept out of the test set

SENTIMENTS = ["positive", "negative", "mixed", "neutral"]
FONT = "Arial"


async def choose(db) -> tuple[list[int], list[int]]:
    """The development and test sets' review IDs, each in a shuffled order."""
    rows = (await db.execute(text("""
        select r.id, r.rating, r.text,
               exists (select 1 from review_themes rt where rt.review_id = r.id and rt.sentiment = 'neutral'
                       and rt.theme_id = any(:dishes)) as neutral_dish
        from reviews r where r.entity_id = :entity
        order by md5(r.id::text || :seed)"""), {"entity": ENTITY_ID, "seed": SEED, "dishes": DISH_THEMES})).all()
    # Reviews too short for the model go straight to General, so there is nothing to label.
    usable = [row for row in rows if has_enough_text(row.text) and row.id not in SEEN]
    taken: set[int] = set()

    def take(condition, count: int) -> list[int]:
        picked = [row.id for row in usable if row.id not in taken and condition(row)][:count]
        taken.update(picked)
        return picked

    # The test set first, from every review, so the development set's enrichment doesn't thin it.
    test = take(lambda row: True, SET_SIZE)
    enriched = lambda row: row.rating is not None and row.rating >= 4 and row.neutral_dish  # noqa: E731
    dev = list(KNOWN) + take(enriched, DEV_ENRICHED - len(KNOWN))
    dev += take(lambda row: True, SET_SIZE - len(dev))
    random.Random(SEED).shuffle(dev)
    return dev, test


def write_workbook(path: Path, reviews: list[tuple[int, str]], title: str) -> None:
    workbook = Workbook()
    guide = workbook.active
    guide.title = "How to label"
    sheet = workbook.create_sheet("Labels")

    regular = Font(name=FONT, size=10)
    bold = Font(name=FONT, size=10, bold=True)
    input_fill = PatternFill("solid", start_color="FFF8D6")
    header_fill = PatternFill("solid", start_color="E8ECF4")
    thin = Side(style="thin", color="C8CCD4")
    grid = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap_top = Alignment(wrap_text=True, vertical="top")

    # How to label.
    lines = [
        (f"{title}: hand labels", Font(name=FONT, size=14, bold=True)),
        ("", regular),
        ("What to do", bold),
        ("On the Labels sheet, read each review and write down every topic it raises, one per Topic cell, in your own "
         "words, and choose the reviewer's sentiment about it in the Sentiment cell beside it. Only the yellow cells "
         "are for editing. Use as many topic cells as the review needs and leave the rest empty.", regular),
        ("", regular),
        ("Topics", bold),
        ("A topic is something specific the reviewer comments on or mentions: a dish or drink, an aspect of the "
         "service, the wait, the price, the room, the reservation, the location, the restaurant's reputation.", regular),
        ("Be as specific as the review is: \"gumbo\", \"mac and cheese\", \"server's recommendations\", \"wait for a "
         "table\", \"noise\". Don't group things yet (write \"gumbo\", not \"sides\"); grouping comes afterwards.",
         regular),
        ("Use the same words for the same thing each time (\"wait for a table\", not \"table wait\" the next time). "
         "Small differences are fine; they are tidied up when the topics are grouped.", regular),
        ("One topic per distinct point: \"the gumbo was great but the jambalaya was dry\" is two topics.", regular),
        ("A verdict on the whole visit (\"great place\", \"highly recommend\", \"meh\") goes in as the topic "
         "\"Overall\". Record it whether or not the review has other topics.", regular),
        ("", regular),
        ("Sentiment, for each topic", bold),
        ("positive / negative: the reviewer likes or dislikes it.", regular),
        ("mixed: both, about that topic (\"the oysters were great but cold\").", regular),
        ("neutral: mentioned with no judgement of it (\"we ordered the rabbit\", \"book a week ahead\").", regular),
        ("A judgement of the meal or the food as a whole covers every dish and drink the review names: \"We had the "
         "rabbit and the cochon. Great meal!\" makes both positive. Anything the review says about the dish itself "
         "overrides it: \"decent\" stays as written. Praise of the service or the room does not cover the dishes.",
         regular),
        ("Judge from the words, not from what you guess the rating was (the rating is not shown, as the classifier "
         "doesn't see it either).", regular),
        ("", regular),
        ("Notes", bold),
        ("Anything worth remembering: an unclear case, a topic you weren't sure how to name.", regular),
        ("", regular),
        ("Example (a made-up review)", bold),
        ("\"We had the gator bites and the rabbit, both delicious, but waited 40 minutes past our reservation. "
         "Parking was a nightmare. Still worth it!\"", regular),
        ("fried alligator: positive | rabbit and dumplings: positive | wait past reservation time: negative | "
         "parking: negative | Overall: positive", regular),
        ("", regular),
        ("Each review is shown in full. A very long one may not fit its row: select the cell and read the rest in "
         "the formula bar (drag its bottom edge down to enlarge it).", regular),
        (f"Reviews longer than {MAX_REVIEW_CHARS:,} characters are cut where the classifier cuts them (marked \"…\"), "
         "so label only what is shown.", regular),
    ]
    guide.column_dimensions["A"].width = 120
    for row, (value, font) in enumerate(lines, start=1):
        cell = guide.cell(row=row, column=1, value=value)
        cell.font = font
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    # Labels: review ID, text, then topic and sentiment pairs, then notes.
    first_topic_column = 3
    notes_column = first_topic_column + 2 * TOPIC_SLOTS
    headings = {1: "Review ID", 2: "Review", notes_column: "Notes"}
    for slot in range(TOPIC_SLOTS):
        headings[first_topic_column + 2 * slot] = f"Topic {slot + 1}"
        headings[first_topic_column + 2 * slot + 1] = "Sentiment"
    for column, heading in headings.items():
        cell = sheet.cell(row=1, column=column, value=heading)
        cell.font, cell.fill, cell.border = bold, header_fill, grid
        cell.alignment = Alignment(vertical="bottom", horizontal="center", wrap_text=True)

    choice = DataValidation(type="list", formula1=f'"{",".join(SENTIMENTS)}"', allow_blank=True,
                            showErrorMessage=True, errorTitle="Sentiment",
                            error="Choose positive, negative, mixed or neutral, or leave the cell empty.")
    sheet.add_data_validation(choice)
    for row, (review_id, review_text) in enumerate(reviews, start=2):
        shown = review_text if len(review_text) <= MAX_REVIEW_CHARS else review_text[:MAX_REVIEW_CHARS] + "…"
        id_cell = sheet.cell(row=row, column=1, value=review_id)
        text_cell = sheet.cell(row=row, column=2, value=shown)
        for cell in (id_cell, text_cell):
            cell.font, cell.alignment, cell.border = regular, wrap_top, grid
        for column in range(first_topic_column, notes_column + 1):
            cell = sheet.cell(row=row, column=column)
            cell.fill, cell.border, cell.font, cell.alignment = input_fill, grid, regular, wrap_top
        # About 130 characters to a line at this width, 13 points to a line. Excel allows rows up to 409
        # points (about 4,000 characters); the rest of a longer review is in the formula bar.
        lines = len(shown) // 130 + 1 + shown.count("\n")
        sheet.row_dimensions[row].height = min(409, max(30, 13 * lines))
    last_row = len(reviews) + 1
    for slot in range(TOPIC_SLOTS):
        letter = get_column_letter(first_topic_column + 2 * slot + 1)
        choice.add(f"{letter}2:{letter}{last_row}")

    sheet.column_dimensions["A"].width = 9
    sheet.column_dimensions["B"].width = 110
    for slot in range(TOPIC_SLOTS):
        sheet.column_dimensions[get_column_letter(first_topic_column + 2 * slot)].width = 22
        sheet.column_dimensions[get_column_letter(first_topic_column + 2 * slot + 1)].width = 11
    sheet.column_dimensions[get_column_letter(notes_column)].width = 30
    sheet.freeze_panes = "C2"
    workbook.active = 1
    workbook.save(path)


async def main() -> None:
    async with async_session_maker() as db:
        dev, test = await choose(db)
        for name, ids, title in [("dev", dev, "Development set"), ("test", test, "Test set")]:
            id_path = HERE / f"cochon_{name}_ids.txt"
            id_path.write_text("\n".join(str(review_id) for review_id in ids) + "\n", encoding="utf-8")
            book_path = HERE / f"cochon_{name}.xlsx"
            if book_path.exists():
                print(f"{book_path.name} exists; not overwritten (it may hold labels)")
                continue
            texts = dict((await db.execute(text("select id, text from reviews where id = any(:ids)"),
                                           {"ids": ids})).all())
            write_workbook(book_path, [(review_id, texts[review_id]) for review_id in ids], f"Cochon {title.lower()}")
            print(f"Wrote {book_path.name} and {id_path.name}: {len(ids)} reviews")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
