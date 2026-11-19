"""The codebook: the hand-labelled topics grouped into entries, and discovery scored against it.

    python evals/codebook.py [--study cochon|npr] propose [--sets dev test]      (from src/)
    python evals/codebook.py [--study cochon|npr] show [--set dev]
    python evals/codebook.py [--study cochon|npr] score REPORT... [--set dev] [--rejudge]

Each study (labels.py) has its own codebook, <study>_codebook.xlsx; Cochon is the default.

The labels (labels.py) are free-form: each review's topics in the labeller's words. The codebook
groups them at two levels:

- An entry is one topic, whatever the wording: "gumbo" and "duck and andouille gumbo" are the entry
  Gumbo, "wait for a table" and "table wait" the entry Wait for a table.
- A group is the broader area a theme in a report might cover, such as a kind of dish or one aspect
  of the service: Gumbo and Jambalaya might share a group. A discovered theme may be as specific as an
  entry (a dish that recurs on its own) or as broad as a group, so scoring checks both.

`propose` asks Sonnet to group the topics that aren't in the codebook yet, adding to the existing
entries where they fit, and writes the workbook. Correct it by hand: on the Entries sheet, rename,
regroup, describe, or set Scored to "no" (the "Overall" entry starts that way, because themes may not
be verdicts); on the Topics sheet, change a topic's entry. Proposing again never changes a row that
is already there. By default only the development set's topics are used, so the test set doesn't
shape the codebook while prompts are tuned; add the test set (`--sets dev test`) once tuning is done.

`show` gives each entry's and group's share of the set's randomly chosen reviews (the development
set's enriched half over-represents praised dishes, so its shares would be skewed) and the sentiment
of its mentions. Entries and groups at the theme threshold (4%) are the ones discovery should find.

`score` scores discovery reports (src/themes/*.json, from discover_themes.py). Sonnet judges which
codebook entries each theme covers, from the descriptions; the judgement is saved in judgements/ and
reused until the codebook's entries or the report's themes change (`--rejudge` asks again), so it can
be read and corrected by hand. Each report gets:

- Key entries and key groups covered: the scored entries, and groups, raised by at least 4% of the
  random reviews, and how many some theme covers (a group counts as covered when its covered entries
  hold at least half of its mentions). This is recall.
- Mentions covered: the share of every labelled mention of a scored entry, in all the set's reviews,
  whose entry some theme covers.
- Themes matching no entry: themes for something no labeller wrote down (precision).
- Overlaps: entries more than one theme covers, which classification can't place consistently.
- Spanning groups: themes covering entries from more than one group, the grab-bags a cap on the
  number of themes produced.
"""
import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from anthropic import AsyncAnthropic, transform_schema
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation
from pydantic import BaseModel, Field, ValidationError

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import discover_themes as discover  # noqa: E402
from config import settings  # noqa: E402
from labels import STUDIES, LabelSet, Study, read_labels  # noqa: E402

JUDGEMENT_DIR = HERE / "judgements"
MODEL = discover.MODEL
MIN_SHARE = discover.MIN_THEME_SHARE  # an entry or group this common is one discovery should find
COVERED_GROUP_SHARE = 0.5  # a group is covered when its covered entries hold this share of its mentions
OVERALL = "Overall"  # the entry for verdicts on the whole visit, not scored
MAX_ATTEMPTS = 2
MAX_OUTPUT_TOKENS = 16000  # includes the model's adaptive thinking; more needs streaming


def codebook_path(study: Study) -> Path:
    return HERE / f"{study.key}_codebook.xlsx"


# The codebook.


@dataclass
class Entry:
    name: str
    group: str
    description: str
    scored: bool = True


@dataclass
class Codebook:
    entries: list[Entry] = field(default_factory=list)
    topics: dict[str, str] = field(default_factory=dict)  # normalized topic -> entry name ("" if unmapped)
    proposed: dict[str, str] = field(default_factory=dict)  # normalized topic -> the model's entry

    def entry(self, name: str) -> Entry | None:
        lowered = name.strip().lower()
        return next((entry for entry in self.entries if entry.name.lower() == lowered), None)

    def entry_of(self, topic: str) -> Entry | None:
        name = self.topics.get(topic, "")
        return self.entry(name) if name else None

    def fingerprint(self) -> str:
        """Changes when an entry's name or description does, which is all the judge sees."""
        described = sorted((entry.name, entry.description) for entry in self.entries)
        return hashlib.sha256(json.dumps(described).encode()).hexdigest()[:16]


ENTRY_HEADINGS = ["Entry", "Group", "Description", "Scored"]
TOPIC_HEADINGS = ["Topic", "Mentions", "Entry", "Proposed", "Reviews"]


def load_codebook(path: Path) -> tuple[Codebook, list[str]]:
    """The codebook in the workbook, and its problems (an entry used but not defined, a name twice)."""
    codebook, problems = Codebook(), []
    if not path.exists():
        return codebook, problems
    workbook = load_workbook(path, read_only=True)
    for row in list(workbook["Entries"].iter_rows(min_row=2, values_only=True)):
        name, group, description, scored = (list(row) + [None] * 4)[:4]
        if not name or not str(name).strip():
            continue
        name = str(name).strip()
        if codebook.entry(name):
            problems.append(f'Entries: "{name}" appears twice')
            continue
        codebook.entries.append(Entry(
            name=name,
            group=str(group or "").strip() or name,
            description=str(description or "").strip(),
            scored=str(scored or "yes").strip().lower() not in ("no", "n", "false", "0"),
        ))
    for row in workbook["Topics"].iter_rows(min_row=2, values_only=True):
        topic, _, entry_name, proposed = (list(row) + [None] * 4)[:4]
        if not topic:
            continue
        topic = str(topic).strip()
        entry_name = str(entry_name or "").strip()
        entry = codebook.entry(entry_name) if entry_name else None
        if entry_name and entry is None:
            problems.append(f'Topics: "{topic}" is mapped to "{entry_name}", which isn\'t on the Entries sheet')
        codebook.topics[topic] = entry.name if entry else ""
        codebook.proposed[topic] = str(proposed or "").strip()
    return codebook, problems


def topic_mentions(label_sets: list[LabelSet]) -> tuple[Counter, dict[str, list[int]]]:
    """How often each topic was labelled across the sets, and the reviews that raised it."""
    mentions: Counter = Counter()
    reviews: dict[str, list[int]] = defaultdict(list)
    for label_set in label_sets:
        for label in label_set.labels:
            mentions[label.topic] += 1
            reviews[label.topic].append(label.review_id)
    return mentions, reviews


def write_codebook(codebook: Codebook, label_sets: list[LabelSet], path: Path) -> None:
    """Write the whole workbook from the codebook (entries by group, topics by entry)."""
    mentions, reviews = topic_mentions(label_sets)
    workbook = Workbook()
    entries_sheet = workbook.worksheets[0]
    entries_sheet.title = "Entries"
    topics_sheet = workbook.create_sheet("Topics")

    regular, bold = Font(name="Arial", size=10), Font(name="Arial", size=10, bold=True)
    input_fill = PatternFill("solid", start_color="FFF8D6")
    header_fill = PatternFill("solid", start_color="E8ECF4")
    thin = Side(style="thin", color="C8CCD4")
    grid = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap_top = Alignment(wrap_text=True, vertical="top")

    def fill_row(sheet, row: int, values: list, editable: set[int]) -> None:
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=column, value=value)
            cell.font, cell.border, cell.alignment = regular, grid, wrap_top
            if column in editable:
                cell.fill = input_fill

    for sheet, headings in [(entries_sheet, ENTRY_HEADINGS), (topics_sheet, TOPIC_HEADINGS)]:
        for column, heading in enumerate(headings, start=1):
            cell = sheet.cell(row=1, column=column, value=heading)
            cell.font, cell.fill, cell.border = bold, header_fill, grid
        sheet.freeze_panes = "A2"

    ordered_entries = sorted(codebook.entries, key=lambda entry: (entry.group.lower(), entry.name.lower()))
    for row, entry in enumerate(ordered_entries, start=2):
        values = [entry.name, entry.group, entry.description, "yes" if entry.scored else "no"]
        fill_row(entries_sheet, row, values, editable={1, 2, 3, 4})
    last_entry_row = max(len(ordered_entries) + 1, 2)
    scored_choice = DataValidation(type="list", formula1='"yes,no"', allow_blank=True)
    entries_sheet.add_data_validation(scored_choice)
    scored_choice.add(f"D2:D{last_entry_row + 200}")

    def topic_order(topic: str) -> tuple:
        return (codebook.topics[topic] == "", codebook.topics[topic].lower(), -mentions[topic], topic)

    ordered_topics = sorted(codebook.topics, key=topic_order)
    for row, topic in enumerate(ordered_topics, start=2):
        ids = ", ".join(str(review_id) for review_id in sorted(set(reviews.get(topic, []))))
        values = [topic, mentions[topic], codebook.topics[topic], codebook.proposed.get(topic, ""), ids]
        fill_row(topics_sheet, row, values, editable={3})
    # Choosing an entry from the list avoids typos; a name typed that isn't listed gets a warning.
    entry_choice = DataValidation(
        type="list", formula1=f"Entries!$A$2:$A${last_entry_row + 200}", allow_blank=True,
        errorStyle="warning", showErrorMessage=True, errorTitle="Entry",
        error="Not on the Entries sheet. Add it there too, or the codebook won't load.",
    )
    topics_sheet.add_data_validation(entry_choice)
    entry_choice.add(f"C2:C{len(ordered_topics) + 1}")

    for sheet, widths in [(entries_sheet, [28, 24, 80, 9]), (topics_sheet, [34, 10, 28, 28, 40])]:
        for column, width in enumerate(widths):
            sheet.column_dimensions["ABCDE"[column]].width = width
    try:
        workbook.save(path)
    except PermissionError:
        raise SystemExit(f"Can't write {path.name}: close it in Excel and run again.")


# Asking the model.


async def ask(
    client: AsyncAnthropic,
    system: str,
    user_message: str,
    schema: type[BaseModel],
    check: Callable[[BaseModel], None],
    usage: discover.TokenUsage,
) -> BaseModel:
    """Structured output as in discover_themes.request_themes: validated, then checked, one retry."""
    last_error = None
    for _ in range(MAX_ATTEMPTS):
        content = user_message
        if last_error:
            content += f"\n\nYour previous answer was rejected: {last_error}\nTry again."
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": transform_schema(schema)}},
        )
        usage.input_tokens += response.usage.input_tokens
        usage.output_tokens += response.usage.output_tokens
        try:
            if response.stop_reason != "end_turn":
                raise ValueError(f"No complete answer (stop reason: {response.stop_reason}).")
            answer = schema.model_validate_json(next(block.text for block in response.content if block.type == "text"))
            check(answer)
            return answer
        except (ValidationError, ValueError, StopIteration) as error:
            last_error = str(error)
    raise RuntimeError(f"No valid answer after {MAX_ATTEMPTS} attempts: {last_error}")


def check_numbers(numbers: list[int], count: int, what: str, exactly_once: bool) -> None:
    """Raise ValueError unless every number is 1..count, and, if exactly_once, each appears once."""
    outside = sorted({number for number in numbers if not 1 <= number <= count})
    if outside:
        raise ValueError(f"There is no {what} {', '.join(map(str, outside))} (they are numbered 1 to {count}).")
    if exactly_once:
        repeated = sorted(number for number, times in Counter(numbers).items() if times > 1)
        missing = sorted(set(range(1, count + 1)) - set(numbers))
        if repeated:
            raise ValueError(f"Each {what} must be given once; repeated: {', '.join(map(str, repeated))}.")
        if missing:
            raise ValueError(f"Every {what} must be given; missing: {', '.join(map(str, missing))}.")


# Proposing entries.


PROPOSE_SYSTEM = """You help build a codebook from hand labels of customer reviews of {subject}.
A person read each review and wrote down every topic it raises, in their own words. You group
their topics into codebook entries, and the entries into groups.

An entry is one topic. Different wordings of the same thing go together ("wait for a table",
"table wait", "waited an hour for a seat"; "crashes on start-up", "won't open"), and so does a thing
with its variants ("gumbo", "duck and andouille gumbo"). Distinct things stay apart even when
related: "gumbo" and "jambalaya" are two entries, and so are "wait for a table" and "wait for food",
or "crashes" and "slow loading". Name each entry as a topic in 1 to 4 words, never an opinion:
"Service speed", not "Slow service".

Give every entry a group: the broader area that one theme in a report could cover, such as a kind
of dish, one aspect of the service, or a set of related features. Entries in a group are related
items a reader would expect together. A group can hold a single entry. Name groups as topics too.

Topics that give a verdict on the whole ("overall", "great place", "great app", "would recommend")
belong to the entry "{overall}" in the group "{overall}".

Every listed topic belongs to exactly one entry. To add topics to an existing entry, give its exact
name (its group and description are kept). Create a new entry only when no existing one fits, and
describe it in one sentence that says what it covers, so a reader could decide whether a topic
belongs."""


class ProposedEntry(BaseModel):
    name: str = Field(description="1-4 word topic name with no sentiment, or an existing entry's exact name")
    group: str = Field(description="The broader area the entry belongs to")
    description: str = Field(description="One sentence saying what the entry covers")
    topics: list[int] = Field(description="The numbers of the listed topics that belong to this entry")


class Proposal(BaseModel):
    entries: list[ProposedEntry]


async def propose_entries(
    client: AsyncAnthropic,
    subject: str,
    codebook: Codebook,
    new_topics: list[str],
    mentions: Counter,
    usage: discover.TokenUsage,
) -> Proposal:
    existing = "\n".join(
        f"- {entry.name} (group: {entry.group}): {entry.description}" for entry in codebook.entries
    ) or "(none yet)"
    numbered = "\n".join(
        f"[{number}] {topic} ({mentions[topic]} mention{'s' if mentions[topic] != 1 else ''})"
        for number, topic in enumerate(new_topics, start=1)
    )
    user_message = f"Existing entries:\n{existing}\n\nTopics to place:\n{numbered}"

    def check(proposal: BaseModel) -> None:
        assert isinstance(proposal, Proposal)
        for entry in proposal.entries:
            if not entry.name.strip() or not entry.group.strip():
                raise ValueError("Every entry needs a name and a group.")
        numbers = [number for entry in proposal.entries for number in entry.topics]
        check_numbers(numbers, len(new_topics), "topic", exactly_once=True)

    system = PROPOSE_SYSTEM.format(subject=subject, overall=OVERALL)
    result = await ask(client, system, user_message, Proposal, check, usage)
    assert isinstance(result, Proposal)
    return result


def apply_proposal(codebook: Codebook, proposal: Proposal, new_topics: list[str]) -> int:
    """Add the proposed entries and map the new topics; return how many entries are new."""
    added = 0
    for proposed in proposal.entries:
        entry = codebook.entry(proposed.name)
        if entry is None:
            name = " ".join(proposed.name.split())
            entry = Entry(
                name=name,
                group=" ".join(proposed.group.split()),
                description=proposed.description.strip(),
                scored=name.lower() != OVERALL.lower(),
            )
            codebook.entries.append(entry)
            added += 1
        for number in proposed.topics:
            topic = new_topics[number - 1]
            codebook.topics[topic] = entry.name
            codebook.proposed[topic] = entry.name
    return added


async def propose(study: Study, set_names: list[str]) -> None:
    path = codebook_path(study)
    codebook = loaded_codebook(study)
    label_sets = [checked_labels(study, name) for name in set_names]
    mentions, _ = topic_mentions(label_sets)
    if not mentions:
        raise SystemExit(f"No topics are labelled in {study.name}'s {' or '.join(set_names)} set yet.")
    new_topics = sorted(topic for topic in mentions if topic not in codebook.topics)
    if not new_topics:
        print("Every labelled topic is in the codebook already.")
        write_codebook(codebook, label_sets, path)  # refreshes the mention counts
        return
    print(f"Asking {MODEL} to place {len(new_topics)} topics ({len(codebook.entries)} entries so far)...")
    usage = discover.TokenUsage()
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    proposal = await propose_entries(client, study.subject, codebook, new_topics, mentions, usage)
    added = apply_proposal(codebook, proposal, new_topics)
    write_codebook(codebook, label_sets, path)
    groups = {entry.group for entry in codebook.entries}
    print(
        f"Wrote {path.name}: {added} new entries, {len(codebook.entries)} in {len(groups)} groups "
        f"({usage.input_tokens} input / {usage.output_tokens} output tokens). Check it by hand."
    )


def loaded_codebook(study: Study) -> Codebook:
    codebook, problems = load_codebook(codebook_path(study))
    if problems:
        raise SystemExit(f"Fix {codebook_path(study).name} first:\n  " + "\n  ".join(problems))
    return codebook


def checked_labels(study: Study, name: str) -> LabelSet:
    label_set = read_labels(study, name)
    if label_set.problems:
        command = f"python evals/labels.py --study {study.key} {name}"
        raise SystemExit(f"The {name} labels have problems ({command}):\n  " + "\n  ".join(label_set.problems))
    return label_set


# Shares.


@dataclass
class Prevalence:
    reviews: set[int] = field(default_factory=set)  # every labelled review raising it
    random_reviews: set[int] = field(default_factory=set)  # those chosen at random
    sentiments: Counter = field(default_factory=Counter)  # of its mentions

    def share(self, random_count: int) -> float:
        return len(self.random_reviews) / random_count if random_count else 0.0


def prevalence(codebook: Codebook, label_set: LabelSet) -> tuple[dict[str, Prevalence], dict[str, Prevalence], list[str]]:
    """Each entry's and each group's reviews and sentiments in the set, and the topics not yet mapped."""
    random_ids = set(label_set.random_ids)
    by_entry: dict[str, Prevalence] = defaultdict(Prevalence)
    by_group: dict[str, Prevalence] = defaultdict(Prevalence)
    unmapped = set()
    for label in label_set.labels:
        entry = codebook.entry_of(label.topic)
        if entry is None:
            unmapped.add(label.topic)
            continue
        for counted in (by_entry[entry.name], by_group[entry.group]):
            counted.reviews.add(label.review_id)
            if label.review_id in random_ids:
                counted.random_reviews.add(label.review_id)
        by_entry[entry.name].sentiments[label.sentiment] += 1
        by_group[entry.group].sentiments[label.sentiment] += 1
    return by_entry, by_group, sorted(unmapped)


def show(study: Study, set_name: str) -> None:
    codebook = loaded_codebook(study)
    label_set = checked_labels(study, set_name)
    random_count = len(label_set.random_ids)
    by_entry, by_group, unmapped = prevalence(codebook, label_set)
    print(
        f"{study.name} {set_name}: {len(label_set.labelled_ids)} of {len(label_set.review_ids)} reviews labelled, "
        f"{random_count} of them chosen at random. Shares are of those; * marks {MIN_SHARE:.0%} or more.\n"
    )
    print(f"{'':44}{'reviews':>8}{'random':>8}{'share':>7}   positive/negative/mixed/neutral")

    def line(name: str, counted: Prevalence, indent: str, scored: bool) -> str:
        share = counted.share(random_count)
        mark = "*" if share >= MIN_SHARE and scored else " "
        sentiments = "/".join(str(counted.sentiments[sentiment]) for sentiment in ("positive", "negative", "mixed", "neutral"))
        label = f"{indent}{name}" + ("" if scored else " (not scored)")
        return f"{label[:43]:44}{len(counted.reviews):>8}{len(counted.random_reviews):>8}{share:>6.0%}{mark}  {sentiments}"

    groups = sorted(by_group, key=lambda group: -len(by_group[group].reviews))
    for group in groups:
        entries = sorted(
            (entry for entry in codebook.entries if entry.group == group and entry.name in by_entry),
            key=lambda entry: -len(by_entry[entry.name].reviews),
        )
        print(line(group, by_group[group], "", any(entry.scored for entry in entries)))
        if len(entries) == 1 and entries[0].name == group:
            continue  # a group of one entry with the same name: one line is enough
        for entry in entries:
            print(line(entry.name, by_entry[entry.name], "  ", entry.scored))
    if unmapped:
        print(f"\n{len(unmapped)} topics aren't in the codebook yet (run propose): {', '.join(unmapped[:20])}"
              + (" ..." if len(unmapped) > 20 else ""))


# Scoring discovery.


JUDGE_SYSTEM = """You compare the themes an analysis found in customer reviews of {subject} with a
codebook: topics people labelled by hand in the reviews, grouped into entries.

For each theme, list the codebook entries it covers. A theme covers an entry when a review raising
that entry's topic belongs in the theme, judged by the theme's description, whatever the review's
sentiment. A broad theme can cover many entries, and a theme can cover none. An entry can be covered
by several themes when their scopes overlap. Judge by each description's scope, not by the names
alone, and don't stretch it: list an entry only if a reader deciding from the description would put
such a review in the theme."""


class ThemeCoverage(BaseModel):
    theme: int = Field(description="The theme's number")
    entries: list[int] = Field(description="The numbers of the codebook entries the theme covers (may be empty)")


class Judgement(BaseModel):
    themes: list[ThemeCoverage]


async def judge(
    client: AsyncAnthropic, subject: str, codebook: Codebook, themes: list[dict], usage: discover.TokenUsage
) -> dict[str, list[str]]:
    """Which entries each theme covers, by name."""
    numbered_themes = "\n".join(
        f"[{number}] {theme['name']}: {theme['description']}" for number, theme in enumerate(themes, start=1)
    )
    numbered_entries = "\n".join(
        f"[{number}] {entry.name}: {entry.description}" for number, entry in enumerate(codebook.entries, start=1)
    )
    user_message = f"Themes:\n{numbered_themes}\n\nCodebook entries:\n{numbered_entries}"

    def check(judgement: BaseModel) -> None:
        assert isinstance(judgement, Judgement)
        check_numbers([item.theme for item in judgement.themes], len(themes), "theme", exactly_once=True)
        entries = [number for item in judgement.themes for number in item.entries]
        check_numbers(entries, len(codebook.entries), "entry", exactly_once=False)

    result = await ask(client, JUDGE_SYSTEM.format(subject=subject), user_message, Judgement, check, usage)
    assert isinstance(result, Judgement)
    covers = {}
    for item in sorted(result.themes, key=lambda item: item.theme):
        names = [codebook.entries[number - 1].name for number in dict.fromkeys(item.entries)]
        covers[themes[item.theme - 1]["name"]] = names
    return covers


async def judgement_for(
    client: AsyncAnthropic, study: Study, codebook: Codebook, report_path: Path, rejudge: bool
) -> dict[str, list[str]]:
    """The saved judgement of a report if it still fits the codebook and the report, else a new one."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report["entity"]["id"] != study.entity_id:
        raise SystemExit(f"{report_path.name} is a report on entity {report['entity']['id']}, not {study.name}.")
    themes = [{"name": theme["name"], "description": theme["description"]} for theme in report["themes"]]
    saved_path = JUDGEMENT_DIR / f"{report_path.stem}.json"
    if saved_path.exists() and not rejudge:
        saved = json.loads(saved_path.read_text(encoding="utf-8"))
        if saved["codebook"] == codebook.fingerprint() and saved["themes"] == themes:
            return saved["covers"]
        print(f"  {saved_path.name} was judged against other entries or themes; judging again")
    usage = discover.TokenUsage()
    covers = await judge(client, study.subject, codebook, themes, usage)
    JUDGEMENT_DIR.mkdir(exist_ok=True)
    saved_path.write_text(json.dumps({
        "report": report_path.name,
        "prompt_version": report.get("prompt_version"),
        "codebook": codebook.fingerprint(),
        "model": MODEL,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "themes": themes,
        "covers": covers,
    }, indent=2), encoding="utf-8")
    print(f"  judged by {MODEL} ({usage.input_tokens} input / {usage.output_tokens} output tokens), saved to "
          f"{saved_path.relative_to(HERE.parent)}")
    return covers


@dataclass
class Score:
    themes: list[str]
    random_count: int
    key_entries: dict[str, float]  # scored entry -> share of random reviews, at MIN_SHARE or more
    key_groups: dict[str, float]
    missed_entries: list[str]
    missed_groups: list[str]
    mentions: int  # labelled mentions of scored entries
    mentions_covered: int
    unmatched_themes: list[str]  # cover no entry at all
    verdict_themes: list[str]  # cover only unscored entries, such as Overall
    overlaps: dict[str, list[str]]  # scored entry -> the themes covering it, when more than one
    spanning: dict[str, list[str]]  # theme -> the groups of the scored entries it covers, when more than one


def score_themes(codebook: Codebook, label_set: LabelSet, covers: dict[str, list[str]]) -> Score:
    random_count = len(label_set.random_ids)
    by_entry, by_group, _ = prevalence(codebook, label_set)
    scored = {entry.name: entry for entry in codebook.entries if entry.scored}
    covering: dict[str, list[str]] = defaultdict(list)  # entry -> themes
    for theme, names in covers.items():
        for name in names:
            covering[name].append(theme)

    key_entries = {
        name: counted.share(random_count)
        for name, counted in by_entry.items()
        if name in scored and counted.share(random_count) >= MIN_SHARE
    }
    scored_groups = {entry.group for entry in scored.values()}
    key_groups = {
        group: counted.share(random_count)
        for group, counted in by_group.items()
        if group in scored_groups and counted.share(random_count) >= MIN_SHARE
    }

    mentions = mentions_covered = 0
    group_mentions: Counter = Counter()
    group_covered: Counter = Counter()
    for label in label_set.labels:
        entry = codebook.entry_of(label.topic)
        if entry is None or not entry.scored:
            continue
        mentions += 1
        group_mentions[entry.group] += 1
        if covering.get(entry.name):
            mentions_covered += 1
            group_covered[entry.group] += 1
    missed_groups = [
        group for group in key_groups if group_covered[group] < COVERED_GROUP_SHARE * group_mentions[group]
    ]

    unmatched, verdicts, spanning = [], [], {}
    for theme, names in covers.items():
        if not names:
            unmatched.append(theme)
        elif not any(name in scored for name in names):
            verdicts.append(theme)
        groups = sorted({scored[name].group for name in names if name in scored})
        if len(groups) > 1:
            spanning[theme] = groups
    return Score(
        themes=list(covers),
        random_count=random_count,
        key_entries=key_entries,
        key_groups=key_groups,
        missed_entries=[name for name in key_entries if not covering.get(name)],
        missed_groups=missed_groups,
        mentions=mentions,
        mentions_covered=mentions_covered,
        unmatched_themes=unmatched,
        verdict_themes=verdicts,
        overlaps={name: themes for name, themes in covering.items() if name in scored and len(themes) > 1},
        spanning=spanning,
    )


def print_score(name: str, result: Score) -> None:
    def shares(items: list[str], table: dict[str, float]) -> str:
        return ", ".join(f"{item} ({table[item]:.0%})" for item in sorted(items, key=lambda item: -table[item]))

    covered_entries = len(result.key_entries) - len(result.missed_entries)
    covered_groups = len(result.key_groups) - len(result.missed_groups)
    print(f"  {len(result.themes)} themes, against {result.random_count} random labelled reviews")
    print(f"  Key entries covered: {covered_entries} of {len(result.key_entries)}"
          + (f"; missed: {shares(result.missed_entries, result.key_entries)}" if result.missed_entries else ""))
    print(f"  Key groups covered: {covered_groups} of {len(result.key_groups)}"
          + (f"; missed: {shares(result.missed_groups, result.key_groups)}" if result.missed_groups else ""))
    if result.mentions:
        print(f"  Mentions covered: {result.mentions_covered / result.mentions:.0%} of {result.mentions}")
    if result.unmatched_themes:
        print(f"  Themes matching no entry: {', '.join(result.unmatched_themes)}")
    if result.verdict_themes:
        print(f"  Themes matching only unscored entries: {', '.join(result.verdict_themes)}")
    for entry, themes in sorted(result.overlaps.items()):
        print(f"  Overlap: {entry} is covered by {', '.join(themes)}")
    for theme, groups in sorted(result.spanning.items()):
        print(f"  Spans groups: {theme} covers {', '.join(groups)}")


async def score(study: Study, report_paths: list[Path], set_name: str, rejudge: bool) -> None:
    codebook = loaded_codebook(study)
    if not codebook.entries:
        raise SystemExit("The codebook is empty: label some reviews and run propose first.")
    label_set = checked_labels(study, set_name)
    _, _, unmapped = prevalence(codebook, label_set)
    if unmapped:
        print(f"Note: {len(unmapped)} of the {set_name} set's topics aren't in the codebook and are left out "
              f"(run propose --sets {' '.join(sorted(set(study.sets[:1]) | {set_name}))}).")
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    rows = []
    for report_path in report_paths:
        print(f"\n{report_path.name}")
        covers = await judgement_for(client, study, codebook, report_path, rejudge)
        result = score_themes(codebook, label_set, covers)
        print_score(report_path.name, result)
        rows.append((report_path.stem, result))
    if len(rows) > 1:
        print(f"\n{'report':44}{'themes':>7}{'entries':>9}{'groups':>8}{'mentions':>10}{'no match':>10}"
              f"{'overlaps':>10}{'spanning':>10}")
        for stem, result in rows:
            entries = f"{len(result.key_entries) - len(result.missed_entries)}/{len(result.key_entries)}"
            groups = f"{len(result.key_groups) - len(result.missed_groups)}/{len(result.key_groups)}"
            mentions = f"{result.mentions_covered / result.mentions:.0%}" if result.mentions else "-"
            print(f"{stem[:43]:44}{len(result.themes):>7}{entries:>9}{groups:>8}{mentions:>10}"
                  f"{len(result.unmatched_themes):>10}{len(result.overlaps):>10}{len(result.spanning):>10}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--study", choices=STUDIES, default="cochon")
    commands = parser.add_subparsers(dest="command", required=True)
    propose_parser = commands.add_parser("propose", help="group the labelled topics not in the codebook yet")
    propose_parser.add_argument("--sets", nargs="+", help="the sets whose topics to use (the study's first by default)")
    show_parser = commands.add_parser("show", help="each entry's and group's share of the labelled reviews")
    show_parser.add_argument("--set", help="the set to count (the study's first by default)")
    score_parser = commands.add_parser("score", help="score discovery reports against the codebook")
    score_parser.add_argument("reports", nargs="+", type=Path)
    score_parser.add_argument("--set", help="the set to score against (the study's first by default)")
    score_parser.add_argument("--rejudge", action="store_true", help="ask the model again even if a judgement is saved")
    args = parser.parse_args()
    study = STUDIES[args.study]
    # Cochon's first set is the development set, so the test set shapes nothing until it is named.
    set_names = (args.sets if args.command == "propose" else [args.set] if args.set else None) or [study.sets[0]]
    unknown = [name for name in set_names if name not in study.sets]
    if unknown:
        raise SystemExit(f"{study.name} has no {', '.join(unknown)} set (it has {', '.join(study.sets)})")
    if args.command == "propose":
        asyncio.run(propose(study, set_names))
    elif args.command == "show":
        show(study, set_names[0])
    else:
        missing = [str(path) for path in args.reports if not path.exists()]
        if missing:
            raise SystemExit(f"No such report: {', '.join(missing)}")
        asyncio.run(score(study, args.reports, set_names[0], args.rejudge))


if __name__ == "__main__":
    main()
