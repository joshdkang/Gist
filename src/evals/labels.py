"""Read the hand labels back from a labelling workbook (make_label_sheets.py), and check them.

    python evals/labels.py [dev|test]       (from src/; both sets when none is named)

Checking reports how many reviews are labelled and each problem a row has: a topic without a
sentiment, a sentiment without a topic, a sentiment that isn't one of the four, or the same topic
twice in one review. Run it while labelling; codebook.py refuses labels with problems.

Topics are compared after `normalize_topic` (case and spacing), so "Wait for a table" and
"wait for a  table" are one topic. Grouping wording that differs more is the codebook's job.
"""
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

HERE = Path(__file__).parent

SENTIMENTS = ("positive", "negative", "mixed", "neutral")
SETS = ("dev", "test")


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


def workbook_path(name: str) -> Path:
    return HERE / f"cochon_{name}.xlsx"


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
    result = LabelSet(name=name, review_ids=[], origins=origins)
    for row in rows:
        if not row or row[0] is None:
            continue
        review_id = int(row[0])
        result.review_ids.append(review_id)
        seen: set[str] = set()
        for slot, (topic_column, sentiment_column) in enumerate(pairs, start=1):
            topic = normalize_topic(str(row[topic_column] or "")) if topic_column < len(row) else ""
            sentiment = str(row[sentiment_column] or "").strip().lower() if sentiment_column < len(row) else ""
            where = f"review {review_id}, topic {slot}"
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


def read_labels(name: str) -> LabelSet:
    """A set's labels, with how each of its reviews was chosen (from its ID list)."""
    origins = read_ids(HERE / f"cochon_{name}_ids.txt")
    return parse_labels(workbook_path(name), name, origins)


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


def main(names: list[str]) -> None:
    for name in names:
        if not workbook_path(name).exists():
            print(f"{name}: no workbook (run make_label_sheets.py)")
            continue
        print_check(read_labels(name))


if __name__ == "__main__":
    chosen = sys.argv[1:] or list(SETS)
    unknown = [name for name in chosen if name not in SETS]
    if unknown:
        raise SystemExit(f"Unknown set: {', '.join(unknown)} (choose from {', '.join(SETS)})")
    main(chosen)
