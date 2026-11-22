"""Score classification against the hand labels: a key per set, and the score of a dry run against it.

    python evals/classification.py [--study cochon|npr] key THEMES_REPORT [--set dev]      (from src/)
    python evals/classification.py [--study cochon|npr] score REPORT... [--set dev] [--show N]

The free-form labels (labels.py) name each review's topics in the labeller's words, blind to the
themes. Classification is scored against the themes the entity actually has, so the labels are turned
into a key: for each review, the themes it should be tagged with and the sentiment of each.

`key` drafts it from the labels. Each labelled topic goes to its codebook entry and from there to
the themes that cover the entry (Sonnet's judgement of the saved themes' discovery report, the same
one codebook.py scores discovery with). Several topics under one theme get one sentiment, combined as
the app combines them (positive with negative is mixed). A topic whose entry no theme covers is a "New
point", which the classifier should give a title of its own. A review with nothing but verdicts on
the whole ("Overall") is General. Unscored entries are left out. The draft is written as a workbook,
<study>_<set>_key.xlsx, one row per expected tag, with the labeller's topics beside it: checking it
by hand (a wrong mapping, a sentiment the combining rule got wrong) turns it into the key. The draft
comes from the labels, never from the model's tags, so checking it isn't anchored on the classifier.
A key is never overwritten. It holds review text, so it stays out of git like the labelling
workbooks.

`score` scores classification reports (src/classifications/*.json, from a dry run with
--review-ids) against the key: the key's tags the report has (recall), the report's tags the key
doesn't (stretched themes, or points the labeller didn't write down), new points given a title, and
how often the sentiment of a found tag is the key's, with the table of key against model. `--show`
lists that many disagreements of each kind, to read.

The test set is keyed and scored once, at the end: both commands refuse it without --final.
"""
import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from anthropic import AsyncAnthropic
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import codebook as cb  # noqa: E402
from config import settings  # noqa: E402
from labels import SENTIMENTS, STUDIES, LabelSet, Study  # noqa: E402
from models import Sentiment  # noqa: E402
from theme_stats import combine_sentiments  # noqa: E402

GENERAL = "General"
NEW_POINT = "New point"
NEW_PREFIX = "(new) "  # how a classification report names a tag with a title of its own
KEY_HEADINGS = ["Review ID", "Review", "Theme", "Sentiment", "Your topics", "Note"]
FONT = "Arial"


def key_path(study: Study, set_name: str) -> Path:
    return HERE / f"{study.key}_{set_name}_key.xlsx"


@dataclass
class DraftTag:
    theme: str
    sentiment: str
    topics: list[str]  # "topic (sentiment)" for each labelled topic behind it
    note: str = ""


def draft_key(codebook: cb.Codebook, label_set: LabelSet, covers: dict[str, list[str]]) -> dict[int, list[DraftTag]]:
    """Each review's expected tags, from its labels and which entries each theme covers."""
    covering: dict[str, list[str]] = defaultdict(list)
    for theme, entries in covers.items():
        for entry in entries:
            covering[entry].append(theme)
    by_review: dict[int, list] = defaultdict(list)
    for label in label_set.labels:
        by_review[label.review_id].append(label)

    key: dict[int, list[DraftTag]] = {}
    for review_id in label_set.review_ids:
        themes: dict[str, list] = defaultdict(list)  # theme -> its labels
        notes: dict[str, str] = {}
        new_points: list[DraftTag] = []
        verdicts = []
        for label in by_review.get(review_id, []):
            shown = f"{label.topic} ({label.sentiment})"
            entry = codebook.entry_of(label.topic)
            if entry is not None and not entry.scored:
                verdicts.append(label)
            elif entry is None:
                new_points.append(DraftTag(NEW_POINT, label.sentiment, [shown], "not in the codebook"))
            elif not covering.get(entry.name):
                new_points.append(DraftTag(NEW_POINT, label.sentiment, [shown], f"no theme covers {entry.name}"))
            else:
                for theme in covering[entry.name]:
                    themes[theme].append(label)
                if len(covering[entry.name]) > 1:
                    for theme in covering[entry.name]:
                        notes[theme] = f"{entry.name} is covered by {len(covering[entry.name])} themes: keep one?"
        tags = [
            DraftTag(
                theme,
                combine_sentiments(Sentiment(label.sentiment) for label in labels).value,
                [f"{label.topic} ({label.sentiment})" for label in labels],
                notes.get(theme, ""),
            )
            for theme, labels in themes.items()
        ] + new_points
        if not tags:
            sentiment = combine_sentiments(Sentiment(label.sentiment) for label in verdicts)
            tags = [DraftTag(GENERAL, sentiment.value if sentiment else "neutral",
                             [f"{label.topic} ({label.sentiment})" for label in verdicts], "only a verdict on the whole")]
        key[review_id] = tags
    return key


def write_key(path: Path, study: Study, set_name: str, themes_report: str, themes: list[dict],
              texts: dict[int, str], key: dict[int, list[DraftTag]]) -> None:
    workbook = Workbook()
    guide = workbook.worksheets[0]
    guide.title = "How to check"
    sheet = workbook.create_sheet("Key")
    theme_sheet = workbook.create_sheet("Themes")

    regular = Font(name=FONT, size=10)
    bold = Font(name=FONT, size=10, bold=True)
    input_fill = PatternFill("solid", start_color="FFF8D6")
    header_fill = PatternFill("solid", start_color="E8ECF4")
    thin = Side(style="thin", color="C8CCD4")
    grid = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap_top = Alignment(wrap_text=True, vertical="top")

    lines = [
        (f"{study.name} {set_name} set: classification key", Font(name=FONT, size=14, bold=True)),
        ("", regular),
        ("The Key sheet lists, for each review, the themes it should be tagged with and the reviewer's sentiment about "
         "each. It was drafted from your labels (shown under Your topics): each topic went to its codebook entry and "
         "from there to the theme that covers it, and several topics under one theme were combined (positive with "
         "negative is mixed, neutral gives way to either). Check it and correct the yellow cells.", regular),
        ("", regular),
        ("What to check", bold),
        ("Theme: is this the theme the point belongs to? Choose another from the list, or add a row for a theme the "
         "review should have (Review ID, Theme, Sentiment), or delete a row that is wrong. The Themes sheet has each "
         "theme's description.", regular),
        (f'"{NEW_POINT}": a point no theme covers, which the classifier should give a title of its own. Change it to '
         "a theme if one does cover it.", regular),
        (f'"{GENERAL}": a review with no topic of its own, only a verdict on the whole ("great place"). Use it '
         "only when the review has no other row.", regular),
        ("Sentiment: the reviewer's feeling about that theme's topic only. When the combining rule got it wrong, "
         "correct it.", regular),
        ("Notes flag a topic covered by two themes. Keep the one it belongs to.", regular),
        ("", regular),
        (f"Drafted from {themes_report}; the key scores classification against those themes only.", regular),
    ]
    guide.column_dimensions["A"].width = 120
    for row, (value, font) in enumerate(lines, start=1):
        cell = guide.cell(row=row, column=1, value=value)
        cell.font, cell.alignment = font, Alignment(wrap_text=True, vertical="top")
    guide.cell(row=len(lines) + 2, column=1, value=f"themes report: {themes_report}").font = Font(
        name=FONT, size=8, color="888888")

    names = [theme["name"] for theme in themes] + [GENERAL, NEW_POINT]
    theme_sheet.append(["Theme", "Description"])
    for cell in theme_sheet[1]:
        cell.font, cell.fill = bold, header_fill
    for theme in themes:
        theme_sheet.append([theme["name"], theme["description"]])
    theme_sheet.append([GENERAL, "A review with no discernible topic, only a verdict on the whole."])
    theme_sheet.append([NEW_POINT, "A specific point no theme covers; the classifier gives it a title of its own."])
    for row in theme_sheet.iter_rows(min_row=2):
        for cell in row:
            cell.font, cell.alignment = regular, wrap_top
    theme_sheet.column_dimensions["A"].width = 32
    theme_sheet.column_dimensions["B"].width = 100

    for column, heading in enumerate(KEY_HEADINGS, start=1):
        cell = sheet.cell(row=1, column=column, value=heading)
        cell.font, cell.fill, cell.border = bold, header_fill, grid
    theme_choice = DataValidation(type="list", formula1=f"Themes!$A$2:$A${len(names) + 1}", allow_blank=False,
                                  showErrorMessage=True, errorTitle="Theme", error="Choose a theme from the list.")
    sentiment_choice = DataValidation(type="list", formula1=f'"{",".join(SENTIMENTS)}"', allow_blank=False,
                                      showErrorMessage=True, errorTitle="Sentiment",
                                      error="Choose positive, negative, mixed or neutral.")
    sheet.add_data_validation(theme_choice)
    sheet.add_data_validation(sentiment_choice)
    row = 2
    for review_id, tags in key.items():
        text = texts.get(review_id, "")
        for index, tag in enumerate(tags):
            values = [review_id, text if index == 0 else "", tag.theme, tag.sentiment, ", ".join(tag.topics), tag.note]
            for column, value in enumerate(values, start=1):
                cell = sheet.cell(row=row, column=column, value=value)
                cell.font, cell.alignment, cell.border = regular, wrap_top, grid
                if column in (3, 4):
                    cell.fill = input_fill
            if index == 0:
                lines_needed = len(text) // 100 + 1 + text.count("\n")
                sheet.row_dimensions[row].height = min(409, max(15, 13 * lines_needed))
            row += 1
    theme_choice.add(f"C2:C{row + 200}")
    sentiment_choice.add(f"D2:D{row + 200}")
    for letter, width in zip("ABCDEF", (9, 80, 30, 11, 45, 30)):
        sheet.column_dimensions[letter].width = width
    sheet.freeze_panes = "C2"
    workbook.active = 1
    workbook.save(path)


@dataclass
class Key:
    themes_report: str
    themes: set[str]  # the established themes it was drafted against
    tags: dict[int, dict[str, str]]  # review -> theme -> sentiment (themes and General)
    new_points: dict[int, int]  # review -> how many new points
    problems: list[str] = field(default_factory=list)


def read_key(path: Path) -> Key:
    workbook = load_workbook(path, read_only=True)
    report_line = next(
        (str(row[0]) for row in workbook["How to check"].iter_rows(values_only=True)
         if row and row[0] and str(row[0]).startswith("themes report: ")), "themes report: ?")
    themes = {
        str(row[0]) for row in workbook["Themes"].iter_rows(min_row=2, values_only=True)
        if row and row[0] and row[0] not in (GENERAL, NEW_POINT)
    }
    key = Key(report_line.removeprefix("themes report: "), themes, defaultdict(dict), Counter())
    current = None
    for number, row in enumerate(workbook["Key"].iter_rows(min_row=2, values_only=True), start=2):
        if not row or all(value is None for value in row[:4]):
            continue
        if row[0] is not None:
            current = int(row[0])
        theme = str(row[2] or "").strip()
        sentiment = str(row[3] or "").strip().lower()
        where = f"row {number} (review {current})"
        if current is None:
            key.problems.append(f"{where}: no review ID")
        elif theme == NEW_POINT:
            key.new_points[current] += 1
        elif theme not in themes and theme != GENERAL:
            key.problems.append(f'{where}: "{theme}" is not one of the themes')
        elif sentiment not in SENTIMENTS:
            key.problems.append(f'{where}: "{theme}" has no sentiment' if not sentiment
                                else f'{where}: "{sentiment}" is not a sentiment')
        elif theme in key.tags[current]:
            key.problems.append(f'{where}: "{theme}" appears twice (combine them into one row)')
        else:
            key.tags[current][theme] = sentiment
    for review_id, tags in key.tags.items():
        if GENERAL in tags and len(tags) > 1:
            key.problems.append(f"review {review_id}: General together with other themes")
    return key


@dataclass
class ClassificationScore:
    report: str
    model: str
    prompt_version: int | None
    expected: int = 0  # the key's theme tags
    found: int = 0
    extra: int = 0  # the report's theme tags the key doesn't have
    new_expected: int = 0
    new_found: int = 0
    new_extra: int = 0
    confusion: Counter = field(default_factory=Counter)  # (key sentiment, model sentiment) of found tags
    missed: list[tuple[int, str, str]] = field(default_factory=list)  # review, theme, key sentiment
    extras: list[tuple[int, str, str]] = field(default_factory=list)  # review, theme, model sentiment
    disagreements: list[tuple[int, str, str, str]] = field(default_factory=list)  # review, theme, key, model

    @property
    def agreed(self) -> int:
        return sum(count for (expected, given), count in self.confusion.items() if expected == given)


def score_report(key: Key, report: dict) -> ClassificationScore:
    general_names = {theme["name"] for theme in report["themes"] if theme.get("kind") == "general"} | {GENERAL}
    result = ClassificationScore(report.get("run_id", "?"), report.get("model", "?"), report.get("prompt_version"))
    examples = {example["review_id"]: example for example in report["examples"]}
    missing = [review_id for review_id in key.tags if review_id not in examples]
    if missing:
        raise SystemExit(f"Run {result.report} doesn't have {len(missing)} of the key's reviews (e.g. {missing[0]}).")
    unknown = {
        tag["theme"] for example in examples.values() for tag in example["tags"]
        if not tag["theme"].startswith(NEW_PREFIX) and tag["theme"] not in key.themes | general_names
    }
    if unknown:
        raise SystemExit(f"Run {result.report} has themes the key wasn't drafted against: {', '.join(sorted(unknown))}. "
                         f"The key is for the themes of {key.themes_report}.")
    for review_id, expected in key.tags.items():
        given: dict[str, str | None] = {}
        new_given = 0
        for tag in examples[review_id]["tags"]:
            if tag["theme"].startswith(NEW_PREFIX):
                new_given += 1
            else:
                given[GENERAL if tag["theme"] in general_names else tag["theme"]] = tag["sentiment"]
        for theme, sentiment in expected.items():
            result.expected += 1
            if theme in given:
                result.found += 1
                result.confusion[(sentiment, given[theme])] += 1
                if given[theme] != sentiment:
                    result.disagreements.append((review_id, theme, sentiment, given[theme]))
            else:
                result.missed.append((review_id, theme, sentiment))
        for theme, sentiment in given.items():
            if theme not in expected:
                result.extra += 1
                result.extras.append((review_id, theme, sentiment))
        wanted = key.new_points.get(review_id, 0)
        result.new_expected += wanted
        result.new_found += min(wanted, new_given)
        result.new_extra += max(0, new_given - wanted)
    return result


def percent(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "-"


def print_classification_score(result: ClassificationScore, show: int) -> None:
    print(f"\nRun {result.report} ({result.model}, prompt v{result.prompt_version})")
    print(f"  Key tags found: {result.found} of {result.expected} ({percent(result.found, result.expected)})")
    print(f"  Tags not in the key: {result.extra} ({percent(result.extra, result.found + result.extra)} of the "
          f"report's theme tags)")
    print(f"  New points given a title: {result.new_found} of {result.new_expected}; titles beyond the key: "
          f"{result.new_extra}")
    print(f"  Sentiment of found tags as in the key: {result.agreed} of {result.found} "
          f"({percent(result.agreed, result.found)})")
    print(f"    {'key \\ model':<12}" + "".join(f"{sentiment:>10}" for sentiment in SENTIMENTS))
    for expected in SENTIMENTS:
        print(f"    {expected:<12}" + "".join(f"{result.confusion[(expected, given)]:>10}" for given in SENTIMENTS))
    if show:
        for title, rows in (("Missed", result.missed), ("Not in the key", result.extras),
                            ("Sentiment differs (key, model)", result.disagreements)):
            if rows:
                print(f"  {title}:")
                for row in rows[:show]:
                    print("    " + " | ".join(str(value) for value in row))


async def make_key(study: Study, themes_report: Path, set_name: str) -> None:
    path = key_path(study, set_name)
    if path.exists():
        raise SystemExit(f"{path.name} exists; not overwritten (it may hold corrections).")
    codebook = cb.loaded_codebook(study)
    label_set = cb.checked_labels(study, set_name)
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    covers = await cb.judgement_for(client, study, codebook, themes_report, rejudge=False)
    report = json.loads(themes_report.read_text(encoding="utf-8"))
    key = draft_key(codebook, label_set, covers)

    from sqlalchemy import text  # the database is needed only for the review text
    from database import async_session_maker, engine
    async with async_session_maker() as db:
        texts = dict((await db.execute(text("select id, text from reviews where id = any(:ids)"),
                                       {"ids": list(key)})).all())
    await engine.dispose()
    write_key(path, study, set_name, themes_report.name, report["themes"], texts, key)
    tags = [tag for tags in key.values() for tag in tags]
    counts = Counter(NEW_POINT if tag.theme == NEW_POINT else GENERAL if tag.theme == GENERAL else "theme"
                     for tag in tags)
    flagged = sum(1 for tag in tags if tag.note.endswith("keep one?"))
    print(f"Wrote {path.name}: {len(key)} reviews, {counts['theme']} theme tags, {counts[NEW_POINT]} new points, "
          f"{counts[GENERAL]} General; {flagged} tags flagged as covered by two themes. Check it by hand.")


def score(study: Study, report_paths: list[Path], set_name: str, show: int) -> None:
    path = key_path(study, set_name)
    if not path.exists():
        raise SystemExit(f"No key for {study.name}'s {set_name} set: run `key THEMES_REPORT` first.")
    key = read_key(path)
    if key.problems:
        print(f"{path.name} has problems; fix them first:")
        for problem in key.problems:
            print(f"  {problem}")
        raise SystemExit(1)
    results = []
    for report_path in report_paths:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report["entity"]["id"] != study.entity_id:
            raise SystemExit(f"{report_path.name} is a report on entity {report['entity']['id']}, not {study.name}.")
        result = score_report(key, report)
        print_classification_score(result, show)
        results.append(result)
    if len(results) > 1:
        print(f"\n{'run':>6}{'model':>20}{'prompt':>8}{'found':>8}{'not in key':>12}{'new':>8}{'sentiment':>11}"
              f"{'pos>neu':>9}{'neu>pos':>9}")
        for result in results:
            print(f"{result.report:>6}{result.model:>20}{'v' + str(result.prompt_version):>8}"
                  f"{percent(result.found, result.expected):>8}{result.extra:>12}"
                  f"{f'{result.new_found}/{result.new_expected}':>8}{percent(result.agreed, result.found):>11}"
                  f"{result.confusion[('positive', 'neutral')]:>9}{result.confusion[('neutral', 'positive')]:>9}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--study", choices=STUDIES, default="cochon")
    commands = parser.add_subparsers(dest="command", required=True)
    key_parser = commands.add_parser("key", help="draft the key from the labels and the saved themes' report")
    key_parser.add_argument("themes_report", type=Path, help="the discovery report whose themes the entity has")
    key_parser.add_argument("--set", default=None)
    score_parser = commands.add_parser("score", help="score classification reports against the key")
    score_parser.add_argument("reports", nargs="+", type=Path)
    score_parser.add_argument("--set", default=None)
    score_parser.add_argument("--show", type=int, default=0, help="list this many disagreements of each kind")
    for subparser in (key_parser, score_parser):
        subparser.add_argument("--final", action="store_true",
                               help="allow the test set: it is keyed and scored once, when tuning is done")
    args = parser.parse_args()
    study = STUDIES[args.study]
    set_name = args.set or study.sets[0]
    if set_name not in study.sets:
        raise SystemExit(f"{study.name} has no {set_name} set (it has {', '.join(study.sets)})")
    if set_name == "test" and not args.final:
        raise SystemExit("The test set is keyed and scored once, when tuning is done: add --final then.")
    if args.command == "key":
        asyncio.run(make_key(study, args.themes_report, set_name))
    else:
        score(study, args.reports, set_name, args.show)


if __name__ == "__main__":
    main()
