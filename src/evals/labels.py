"""Read the hand labels back from a labelling workbook (make_label_sheets.py), and check them.

    python evals/labels.py [--study cochon|npr] [dev|test]      (from src/; Cochon and all its sets by default)

A study is one entity's labelled sets (STUDIES): Cochon has a development set for tuning and a test
set; NPR News, a second domain, has the same two sets, so prompts tuned on Cochon can be checked and
fixed on an app before either test set is scored. Each set is an ID list,
<study>_<set>_ids.txt, and a workbook, <study>_<set>.xlsx.

Checking reports how many reviews are labelled and each problem a row has: a topic without a
sentiment, a sentiment without a topic, a sentiment that isn't one of the four, or the same topic
twice in one review. Run it while labelling; codebook.py refuses labels with problems. A review
with more topics than the columns hold continues in the Notes column, as "topic: sentiment" pieces
separated by commas or line breaks; anything else in the notes is a remark and ignored.

Topics are compared after `normalize_topic` (case and spacing), so "Wait for a table" and
"wait for a  table" are one topic. Grouping wording that differs more is the codebook's job.
"""
import argparse
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

HERE = Path(__file__).parent

SENTIMENTS = ("positive", "negative", "mixed", "neutral")


@dataclass(frozen=True)
class Study:
    key: str  # file names start with it
    name: str
    entity_id: int
    subject: str  # how the codebook's prompts name the entity
    sets: tuple[str, ...]  # the first is the one tools use by default


STUDIES = {
    "cochon": Study("cochon", "Cochon", 11, "Cochon (a Cajun restaurant in New Orleans)", ("dev", "test")),
    "npr": Study(
        "npr", "NPR News", 1591,
        "NPR News (an Android app for NPR news stories, podcasts and live public radio)", ("dev", "test"),
    ),
}


@dataclass(frozen=True)
class TopicLabel:
    review_id: int
    topic: str  # normalized
    sentiment: str


@dataclass
class LabelSet:
    name: str
    review_ids: list[int]  # every review in the set, in the workbook's order
    origins: dict[int, str]  # how each review was chosen: known, enriched or random
    labels: list[TopicLabel] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    remarks: list[str] = field(default_factory=list)  # pieces of notes that aren't "topic: sentiment"

    @property
    def labelled_ids(self) -> list[int]:
        """The reviews with at least one topic, in the workbook's order."""
        labelled = {label.review_id for label in self.labels}
        return [review_id for review_id in self.review_ids if review_id in labelled]

    @property
    def random_ids(self) -> list[int]:
        """The labelled reviews drawn at random, whose shares describe the entity's reviews."""
        return [review_id for review_id in self.labelled_ids if self.origins.get(review_id) == "random"]


def read_ids(path: Path) -> dict[int, str]:
    """A set's review IDs, in order, with how each was chosen ("" when the line doesn't say)."""
    ids = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        review_id, _, origin = line.partition("#")
        if review_id.strip():
            ids[int(review_id)] = origin.strip()
    return ids


def normalize_topic(topic: str) -> str:
    """Lower case with single spaces."""
    return " ".join(topic.split()).lower()


def workbook_path(study: Study, name: str) -> Path:
    return HERE / f"{study.key}_{name}.xlsx"


def ids_path(study: Study, name: str) -> Path:
    return HERE / f"{study.key}_{name}_ids.txt"


def parse_labels(path: Path, name: str, origins: dict[int, str]) -> LabelSet:
    """The topic labels in a workbook's Labels sheet, found by their headings, and any problems."""
    sheet = load_workbook(path, read_only=True)["Labels"]
    rows = sheet.iter_rows(values_only=True)
    headings = [str(value or "").strip() for value in next(rows)]
    pairs = [
        (column, column + 1)
        for column, heading in enumerate(headings)
        if heading.startswith("Topic") and column + 1 < len(headings) and headings[column + 1] == "Sentiment"
    ]
    notes_column = next((column for column, heading in enumerate(headings) if heading.startswith("Note")), None)
    result = LabelSet(name=name, review_ids=[], origins=origins)
    for row in rows:
        if not row or row[0] is None:
            continue
        review_id = int(row[0])
        result.review_ids.append(review_id)
        seen: set[str] = set()
        cells = [
            (f"topic {slot}",
             str(row[topic_column] or "") if topic_column < len(row) else "",
             str(row[sentiment_column] or "") if sentiment_column < len(row) else "")
            for slot, (topic_column, sentiment_column) in enumerate(pairs, start=1)
        ]
        if notes_column is not None and notes_column < len(row) and row[notes_column]:
            cells += note_labels(str(row[notes_column]), result.remarks)
        for slot, raw_topic, raw_sentiment in cells:
            topic = normalize_topic(raw_topic)
            sentiment = raw_sentiment.strip().lower()
            where = f"review {review_id}, {slot}"
            if not topic and not sentiment:
                continue
            if not topic:
                result.problems.append(f"{where}: a sentiment ({sentiment}) without a topic")
            elif not sentiment:
                result.problems.append(f'{where}: "{topic}" has no sentiment')
            elif sentiment not in SENTIMENTS:
                result.problems.append(f'{where}: "{topic}" has the sentiment "{sentiment}"')
            elif topic in seen:
                result.problems.append(f'{where}: "{topic}" appears twice (combine them into one, mixed if they differ)')
            else:
                seen.add(topic)
                result.labels.append(TopicLabel(review_id, topic, sentiment))
    return result


def note_labels(notes: str, remarks: list[str]) -> list[tuple[str, str, str]]:
    """The topics in a Notes cell, for a review with more than the columns hold.

    Notes are written "topic: sentiment", separated by commas or line breaks. A piece with no colon
    is a remark ("weird review"), kept in `remarks` and not read as a topic.
    """
    found = []
    for piece in re.split(r"[,\n]", notes):
        piece = piece.strip()
        if not piece:
            continue
        topic, colon, sentiment = piece.rpartition(":")
        if colon:
            found.append(("notes", topic, sentiment))
        else:
            remarks.append(piece)
    return found


def read_labels(study: Study, name: str) -> LabelSet:
    """A set's labels, with how each of its reviews was chosen (from its ID list)."""
    return parse_labels(workbook_path(study, name), name, read_ids(ids_path(study, name)))


def print_check(label_set: LabelSet) -> None:
    labelled = label_set.labelled_ids
    topics = Counter(label.topic for label in label_set.labels)
    print(
        f"{label_set.name}: {len(labelled)} of {len(label_set.review_ids)} reviews labelled, "
        f"{len(label_set.labels)} topics ({len(topics)} different)"
    )
    for problem in label_set.problems:
        print(f"  {problem}")
    if not label_set.problems:
        print("  no problems")
    if label_set.remarks:
        # Only counted: a test set's notes stay unread until it is scored. A remark that is really a
        # topic split by a comma would show up here as an extra remark.
        print(f"  {len(label_set.remarks)} remarks in the notes (not topics)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the hand labels in a study's workbooks.")
    parser.add_argument("--study", choices=STUDIES, default="cochon")
    parser.add_argument("sets", nargs="*", help="the sets to check (all of the study's by default)")
    args = parser.parse_args()
    study = STUDIES[args.study]
    unknown = [name for name in args.sets if name not in study.sets]
    if unknown:
        raise SystemExit(f"{study.name} has no {', '.join(unknown)} set (it has {', '.join(study.sets)})")
    for name in args.sets or study.sets:
        if not workbook_path(study, name).exists():
            print(f"{name}: no workbook (run make_label_sheets.py --study {study.key})")
            continue
        print_check(read_labels(study, name))


if __name__ == "__main__":
    main()
