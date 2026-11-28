"""Calibrate the cutoff of search by meaning against the hand-labelled sets.

    python evals/search_cutoff.py [--set dev|test] [--final] [--show N]

Each scored codebook entry that a set's labels give at least MIN_RELEVANT reviews is a search: the
query is the entry's name ("Wait time", "Gumbo", "Live stream"), as someone would type it, and the
right answers are the set's reviews labelled with a topic in that entry. A review of the set counts
as found when the rule matches it. Two rules are compared:

- absolute: the review's cosine similarity to the query is at least a cutoff;
- relative: its similarity is at least z standard deviations above the mean similarity of all the
  entity's reviews to that query, which adapts to queries whose scores sit higher or lower;
- hybrid: keyword search or the relative rule (with a cutoff of its own), so exact words still
  match and meaning adds the reviews that say it otherwise.

Each rule's parameter is swept, and the one with the best mean F1 over the searches of both studies
is reported, beside keyword search (search.py) and the rule search.py uses (the hybrid with its
MEANING_Z and MEANING_MIN_SIMILARITY) on the same searches. Labels name the topics a
review raises, not every word it says, so a review that mentions a topic in passing may count as a
wrong match here: precision is a lower bound.

Like the other tools, it refuses the test set without --final: the dev sets choose the rule and the
cutoff, and the test sets check them once.
"""
import argparse
import asyncio
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import codebook as cb  # noqa: E402
from labels import STUDIES, Study  # noqa: E402
from sqlalchemy import select  # noqa: E402

from database import async_session_maker, engine  # noqa: E402
from embeddings import configured_embedder  # noqa: E402
from models import Review, ReviewEmbedding  # noqa: E402
from search import MEANING_MIN_SIMILARITY, MEANING_Z, search_filter  # noqa: E402

MIN_RELEVANT = 3
ABSOLUTE_CUTOFFS = [round(0.15 + 0.01 * step, 2) for step in range(36)]  # 0.15 to 0.50
RELATIVE_CUTOFFS = [round(1.0 + 0.1 * step, 1) for step in range(31)]  # 1.0 to 4.0 sd


@dataclass
class Search:
    study: str
    entry: str
    relevant: set[int]
    similarities: dict[int, float] = field(default_factory=dict)  # the set's reviews
    z_scores: dict[int, float] = field(default_factory=dict)
    keyword_matches: set[int] = field(default_factory=set)


def f1(found: set[int], relevant: set[int]) -> tuple[float, float, float]:
    hits = len(found & relevant)
    precision = hits / len(found) if found else 0.0
    recall = hits / len(relevant) if relevant else 0.0
    score = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, score


RULES = {"absolute": ABSOLUTE_CUTOFFS, "relative": RELATIVE_CUTOFFS, "hybrid": RELATIVE_CUTOFFS}


def matches(search: Search, rule: str, cutoff: float) -> set[int]:
    scores = search.similarities if rule == "absolute" else search.z_scores
    found = {review_id for review_id, score in scores.items() if score >= cutoff}
    return found | search.keyword_matches if rule == "hybrid" else found


async def searches_for(study: Study, set_name: str, embedder) -> list[Search]:
    codebook = cb.loaded_codebook(study)
    label_set = cb.checked_labels(study, set_name)
    relevant: dict[str, set[int]] = defaultdict(set)
    for label in label_set.labels:
        entry = codebook.entry_of(label.topic)
        if entry is not None and entry.scored:
            relevant[entry.name].add(label.review_id)
    searches = [Search(study.key, entry, ids) for entry, ids in sorted(relevant.items()) if len(ids) >= MIN_RELEVANT]

    set_ids = set(label_set.review_ids)
    async with async_session_maker() as db:
        rows = (
            await db.execute(
                select(ReviewEmbedding.review_id, ReviewEmbedding.embedding).where(
                    ReviewEmbedding.entity_id == study.entity_id, ReviewEmbedding.model == embedder.model
                )
            )
        ).all()
        if not rows:
            raise SystemExit(f"{study.name} has no embeddings: run embed_reviews.py {study.entity_id} first.")
        ids = [review_id for review_id, _ in rows]
        matrix = np.array([np.asarray(vector, dtype=np.float32) for _, vector in rows])
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        missing = set_ids - set(ids)
        if missing:
            raise SystemExit(f"{len(missing)} of the set's reviews have no embedding.")
        in_set = [position for position, review_id in enumerate(ids) if review_id in set_ids]

        for search in searches:
            query = np.asarray(await embedder.embed_query(search.entry), dtype=np.float32)
            similarity = matrix @ (query / np.linalg.norm(query))
            mean, sd = float(similarity.mean()), float(similarity.std())
            for position in in_set:
                search.similarities[ids[position]] = float(similarity[position])
                search.z_scores[ids[position]] = (float(similarity[position]) - mean) / sd
            condition = search_filter(search.entry)
            search.keyword_matches = set(
                await db.scalars(select(Review.id).where(Review.id.in_(set_ids), condition))
            ) if condition is not None else set()
    return searches


def chosen_matches(search: Search) -> set[int]:
    """What search.py's meaning mode matches: the keywords, or both of its cutoffs."""
    close = {review_id for review_id, similarity in search.similarities.items()
             if search.z_scores[review_id] >= MEANING_Z and similarity >= MEANING_MIN_SIMILARITY}
    return close | search.keyword_matches


def mean_f1(searches: list[Search], rule: str, cutoff: float) -> float:
    return statistics.mean(f1(matches(search, rule, cutoff), search.relevant)[2] for search in searches)


def summary(searches: list[Search], found_by) -> tuple[float, float, float]:
    scores = [f1(found_by(search), search.relevant) for search in searches]
    return tuple(statistics.mean(score[index] for score in scores) for index in range(3))


async def main(set_name: str, show: int) -> None:
    embedder = configured_embedder()
    if embedder is None:
        raise SystemExit("Set VOYAGE_API_KEY in .env first.")
    try:
        by_study = {key: await searches_for(study, set_name, embedder) for key, study in STUDIES.items()}
    finally:
        await engine.dispose()
    every = [search for searches in by_study.values() for search in searches]
    print(f"{set_name} sets: " + ", ".join(f"{STUDIES[key].name} {len(searches)} searches"
                                          for key, searches in by_study.items()))

    best = {}
    for rule, cutoffs in RULES.items():
        # Each study counts equally, whatever its number of searches.
        best[rule] = max(cutoffs, key=lambda cutoff: statistics.mean(
            mean_f1(searches, rule, cutoff) for searches in by_study.values()))

    print(f"\n{'':<28}" + "".join(f"{STUDIES[key].name:>27}" for key in by_study))
    print(f"{'':<28}" + "".join(f"{'precision  recall     F1':>27}" for _ in by_study))
    chosen = f"chosen (z >= {MEANING_Z}, sim >= {MEANING_MIN_SIMILARITY})"
    rows = [("keyword search", lambda search: search.keyword_matches),
            (chosen, lambda search: chosen_matches(search))]
    rows += [(f"{rule} >= {best[rule]}", lambda search, rule=rule: matches(search, rule, best[rule]))
             for rule in RULES]
    for label, found_by in rows:
        cells = "".join(f"{p:>13.0%}{r:>8.0%}{f:>6.0%}" for p, r, f in
                        (summary(by_study[key], found_by) for key in by_study))
        print(f"{label:<28}{cells}")

    print("\nMean F1 across the sweep (both studies):")
    for rule, cutoffs in RULES.items():
        index = cutoffs.index(best[rule])
        nearby = cutoffs[max(0, index - 3): index + 4]
        print(f"  {rule}: " + ", ".join(f"{cutoff}: {mean_f1(every, rule, cutoff):.0%}" for cutoff in nearby))

    if show:
        rule = max(best, key=lambda name: mean_f1(every, name, best[name]))
        print(f"\nPer search, {rule} >= {best[rule]} (found / relevant, precision, recall; keyword recall):")
        ranked = sorted(every, key=lambda search: f1(matches(search, rule, best[rule]), search.relevant)[2])
        for search in ranked[:show] + ranked[-show:]:
            found = matches(search, rule, best[rule])
            precision, recall, _ = f1(found, search.relevant)
            keyword_recall = f1(search.keyword_matches, search.relevant)[1]
            print(f"  {search.study:<7}{search.entry:<34}{len(found):>4} / {len(search.relevant):<4}"
                  f"{precision:>6.0%}{recall:>6.0%}   {keyword_recall:>5.0%}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Calibrate search by meaning against the labelled sets.")
    parser.add_argument("--set", default="dev", choices=["dev", "test"])
    parser.add_argument("--final", action="store_true", help="allow the test set, scored once")
    parser.add_argument("--show", type=int, default=0, help="list the N worst and best searches")
    args = parser.parse_args()
    if args.set == "test" and not args.final:
        raise SystemExit("The test sets check the chosen cutoff once: add --final then.")
    asyncio.run(main(args.set, args.show))
