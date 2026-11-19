"""The eval tooling in evals/: reading hand labels, the codebook workbook, and scoring discovery."""
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
import codebook as cb  # noqa: E402
from labels import LabelSet, TopicLabel, parse_labels  # noqa: E402
from make_label_sheets import GUIDES, write_workbook  # noqa: E402


def labelled_workbook(path: Path, rows: dict[int, list[tuple[str, str]]]) -> Path:
    """A labelling workbook as make_label_sheets writes it, with these topic and sentiment pairs filled in."""
    write_workbook(path, [(review_id, f"Review {review_id}") for review_id in rows], "Test", GUIDES["cochon"])
    workbook = load_workbook(path)
    sheet = workbook["Labels"]
    for row, pairs in enumerate(rows.values(), start=2):
        for slot, (topic, sentiment) in enumerate(pairs):
            sheet.cell(row=row, column=3 + 2 * slot, value=topic or None)
            sheet.cell(row=row, column=4 + 2 * slot, value=sentiment or None)
    workbook.save(path)
    return path


def test_labels_are_read_normalized_with_their_problems(tmp_path):
    path = labelled_workbook(tmp_path / "set.xlsx", {
        1: [("  Wait for a   Table ", "negative"), ("Gumbo", "Positive")],
        2: [("gumbo", ""), ("", "neutral"), ("noise", "great")],
        3: [("rabbit", "positive"), ("Rabbit", "neutral")],
        4: [],
    })
    labels = parse_labels(path, "set", {1: "random", 2: "enriched", 3: "random", 4: "random"})
    assert labels.review_ids == [1, 2, 3, 4]
    assert labels.labels == [
        TopicLabel(1, "wait for a table", "negative"),
        TopicLabel(1, "gumbo", "positive"),
        TopicLabel(3, "rabbit", "positive"),
    ]
    assert len(labels.problems) == 4  # no sentiment, no topic, not a sentiment, the same topic twice
    assert labels.labelled_ids == [1, 3]
    assert labels.random_ids == [1, 3]  # review 4 is random but not labelled yet


def sample_codebook() -> cb.Codebook:
    codebook = cb.Codebook(entries=[
        cb.Entry("Gumbo", "Sides", "The gumbo."),
        cb.Entry("Jambalaya", "Sides", "The jambalaya."),
        cb.Entry("Wait for a table", "Wait for a table", "Waiting to be seated."),
        cb.Entry("Parking", "Parking", "Finding a place to park."),
        cb.Entry(cb.OVERALL, cb.OVERALL, "A verdict on the whole visit.", scored=False),
    ])
    codebook.topics = {
        "gumbo": "Gumbo", "duck gumbo": "Gumbo", "jambalaya": "Jambalaya",
        "wait for a table": "Wait for a table", "parking": "Parking", "overall": cb.OVERALL, "valet": "",
    }
    return codebook


def test_the_codebook_survives_writing_and_reading(tmp_path):
    codebook = sample_codebook()
    labels = LabelSet("dev", [1], {1: "random"}, [TopicLabel(1, "gumbo", "positive")])
    path = tmp_path / "codebook.xlsx"
    cb.write_codebook(codebook, [labels], path)
    loaded, problems = cb.load_codebook(path)
    assert problems == []
    assert [(entry.name, entry.group, entry.scored) for entry in loaded.entries] == sorted(
        [(entry.name, entry.group, entry.scored) for entry in codebook.entries], key=lambda item: (item[1].lower(), item[0].lower())
    )
    assert loaded.topics == codebook.topics

    sheet_book = load_workbook(path)
    sheet_book["Topics"].cell(row=2, column=3, value="Okra")  # an entry the Entries sheet doesn't have
    sheet_book.save(path)
    _, problems = cb.load_codebook(path)
    assert len(problems) == 1 and "Okra" in problems[0]


def test_a_proposal_adds_entries_and_reuses_existing_ones():
    codebook = sample_codebook()
    new_topics = ["seafood gumbo", "over all", "street parking"]
    proposal = cb.Proposal(entries=[
        cb.ProposedEntry(name="gumbo", group="Soups", description="ignored", topics=[1]),
        cb.ProposedEntry(name="Overall", group="Overall", description="ignored", topics=[2]),
        cb.ProposedEntry(name="Street  parking", group="Parking", description="Parking on the street.", topics=[3]),
    ])
    assert cb.apply_proposal(codebook, proposal, new_topics) == 1
    assert codebook.topics["seafood gumbo"] == "Gumbo"  # the existing entry's name and group are kept
    assert codebook.entry("Gumbo").group == "Sides"
    assert codebook.entry("street parking").name == "Street parking"
    assert codebook.proposed["over all"] == cb.OVERALL


def test_numbers_from_the_model_are_checked():
    cb.check_numbers([1, 2, 3], 3, "topic", exactly_once=True)
    with pytest.raises(ValueError, match="no topic 4"):
        cb.check_numbers([1, 4], 3, "topic", exactly_once=False)
    with pytest.raises(ValueError, match="missing: 3"):
        cb.check_numbers([1, 2], 3, "topic", exactly_once=True)
    with pytest.raises(ValueError, match="repeated: 2"):
        cb.check_numbers([1, 2, 2, 3], 3, "topic", exactly_once=True)


def test_discovery_is_scored_against_the_codebook():
    codebook = sample_codebook()
    labels = [
        TopicLabel(1, "gumbo", "positive"), TopicLabel(1, "overall", "positive"),
        TopicLabel(2, "duck gumbo", "neutral"), TopicLabel(2, "wait for a table", "negative"),
        TopicLabel(3, "jambalaya", "positive"), TopicLabel(3, "parking", "negative"),
        TopicLabel(4, "wait for a table", "negative"), TopicLabel(4, "valet", "negative"),
    ]
    # Review 4 was chosen for enrichment, so it counts in the mentions but not in the shares.
    origins = {1: "random", 2: "random", 3: "random", 4: "enriched"}
    label_set = LabelSet("dev", [1, 2, 3, 4], origins, labels)
    covers = {
        "Sides": ["Gumbo", "Jambalaya"],
        "Gumbo and wait": ["Gumbo", "Wait for a table"],
        "Hype": [],
        "Overall experience": [cb.OVERALL],
    }
    result = cb.score_themes(codebook, label_set, covers)
    assert result.random_count == 3
    assert result.key_entries == {"Gumbo": 2 / 3, "Jambalaya": 1 / 3, "Wait for a table": 1 / 3, "Parking": 1 / 3}
    assert result.missed_entries == ["Parking"]
    assert set(result.key_groups) == {"Sides", "Wait for a table", "Parking"}
    assert result.missed_groups == ["Parking"]
    # Six mentions of scored entries (overall isn't scored, valet isn't mapped), five covered.
    assert (result.mentions, result.mentions_covered) == (6, 5)
    assert result.unmatched_themes == ["Hype"]
    assert result.verdict_themes == ["Overall experience"]
    assert result.overlaps == {"Gumbo": ["Sides", "Gumbo and wait"]}
    assert result.spanning == {"Gumbo and wait": ["Sides", "Wait for a table"]}


def test_a_group_is_covered_when_its_covered_entries_hold_half_its_mentions():
    codebook = sample_codebook()
    labels = [TopicLabel(review_id, "jambalaya", "positive") for review_id in (1, 2, 3)]
    labels.append(TopicLabel(4, "gumbo", "positive"))
    label_set = LabelSet("dev", [1, 2, 3, 4], dict.fromkeys([1, 2, 3, 4], "random"), labels)
    assert cb.score_themes(codebook, label_set, {"Gumbo": ["Gumbo"]}).missed_groups == ["Sides"]
    assert cb.score_themes(codebook, label_set, {"Jambalaya": ["Jambalaya"]}).missed_groups == []
