# Decisions

A running record of direction changes and prompt versions.

## Direction changes

- **Generalized beyond restaurants.** Gist started as a Yelp restaurant tool. It now takes any reviewed thing (an "entity") and any review file. Yelp is the first dataset, loaded as public sample entities.
- **Added users.** Supabase Auth for accounts. Entities are owned by users or are public samples. Authorization is enforced in FastAPI.
- **Removed taxonomies.** The fixed category list (Food Quality, Service, ...) is gone. Themes are discovered per entity, and domain context in the prompt comes from the entity's name and description.
- **Two system themes per entity.** `other` (specific feedback no theme covers) and `general` (no discernible topic), created by code with the entity. A rising `other` share signals stale themes and triggers rediscovery. `general` is low-information noise and is reported separately.
- **Themes name topics, not opinions** (aspect-based sentiment analysis). Sentiment lives only on `review_themes`, so a trend can show sentiment improving within one theme.
- **No theme merging, retiring, or versioning for now.** Themes persist and accumulate. Duplicates are prevented by sentiment-neutral names and by showing the model the existing themes on rediscovery. Merging can be added later as a reversible, user-confirmed action. This replaced an earlier design with `version`, `is_active`, and `merged_into_id`.
- **One ingestion path.** A generic CSV ingestion with a column mapping. Yelp is loaded through it by a script, so there is no adapter interface.
- **Baseline migration is pure creation.** The old `businesses`/`feedback` tables were dropped by hand and the Yelp data was reloaded from the source files.

## Theme discovery prompt

### v1 (2026-10-16)

Rewritten from the restaurant-only script.

- Removed the hardcoded restaurant category enum and the per-theme category field.
- Parameterized by entity name and optional description. The prompt text no longer mentions restaurants, and its examples are from other domains ("Checkout speed", "Customer support responsiveness").
- Added the rules from the theme conventions: topic not opinion, both praise and complaints fit, specific but recurring, non-overlapping scope in the description, no catch-all themes.
- Review order is shuffled and ratings are not shown to the model, so position and stars don't hint at sentiment.
- Output is validated: 8-12 themes, unique names, no `other`/`general`. One retry sends back the validation error.
- Structured output uses `output_config.format` with a schema from `transform_schema`, not forced tool use. The first attempt used a forced `tool_choice` and the API rejected it with a 400 ("type tool and any are not supported for this model"), because Sonnet 5.5 doesn't support forced tool use. That failed run is still in `analysis_runs` as run 4 with zero tokens.

What was wrong with the output (first real run: Cochon, entity 11, two independent samples of 600 reviews, runs 15 and 16, about 120k input and 1k output tokens each):

- **Stable overall.** About ten of twelve themes match across the two samples (server attentiveness, host and seating, price and portions, atmosphere, hype versus expectations, fried alligator, wood-fired oysters, the signature cochon dish, seasoning and saltiness, wait times). Differences are granularity at the edges: one run splits "Reservations and wait times" from "Meal pacing", the other merges them, and one adds "Complimentary bread and rolls". By the stability rule (only enlarge the sample if the lists differ noticeably) the sample size stays at 120 per rating.
- **Dish-level themes crowd out breadth.** Four or five of the twelve slots go to individual dishes. Neither run has drinks, location, or a broader food-quality theme, and both sit at the 12-theme cap. Open question: raise the cap, or tell the model to prefer broader topics over single menu items.
- **Overlap.** "Host stand and seating" overlaps "Reservations and wait times", and run 2's "Rabbit, ribs and other mains" is a catch-all of whatever dishes were left.
- **Cited examples are unreliable.** Many cited reviews barely mention the theme (a hostess complaint cited for rabbit and dumplings, a five-star review cited for bread). Don't use discovery citations as the displayed example quotes. Take examples from classification results instead.
- **Negative-leaning examples under neutral names are fine.** "Seasoning and saltiness" is a topic that can hold praise, so the neutral-name convention is working as intended even though the sample is mostly complaints.

### v2 (2026-10-16)

Changes from v1:

- Added: cover the distinct aspects of the experience customers discuss. Give a single item (one dish, one product feature) its own theme only if it comes up in a large share of reviews on its own; otherwise group related items into one coherent theme.
- Strengthened the overlap rule: compare every pair of themes, and if a typical review could reasonably belong to both, combine them.
- Removed the review citations from the prompt and the output schema (`example_review_numbers`), since they were unreliable. Example quotes will come from classification results.

Results (Cochon, entity 11, runs 17 and 18, about 118k and 119k input and under 1k output tokens each; nothing saved):

- **Better breadth.** Dish themes dropped from four or five single dishes to three or four groups (appetizers, entrees, sides and desserts, or pork, oysters and alligator, other dishes), and both runs now include drinks. Run 1 also found cleanliness and dietary needs, and run 2 found bread and rolls.
- **Less stable than v1.** Only about seven of twelve themes match across the two runs (server, pacing, seasoning, price, atmosphere, drinks, and host and reservations in some form). "Hype and expectations" and "Cleanliness and dietary needs" appear in only one run, and "Bread and Rolls" in only one. The food themes are partitioned differently (by protein in one run, by course in the other), because there is no obvious axis for grouping dishes. A larger sample won't fix that.
- **Overlap rule only partly worked.** Run 2 still has "Host Stand and Seating", "Reservations and Wait Times", and "Pacing and Order Timing" covering overlapping ground, and "Atmosphere and Noise" and "Drinks and Bar" both mention the chef's counter.
- **Grab-bag themes.** "Other dishes, sides and desserts" is a residual bucket whose name echoes the system `other` theme, and "Cleanliness, food safety and dietary needs" joins three separate topics.
- **Casing varies** (Title Case in one run, sentence case in the other).

Larger sample (v2 prompt, `--per-level 300`, Cochon, runs 22 and 23): 1,324 reviews each, because Cochon has only 168 one-star and 256 two-star reviews, so those levels were taken whole (plus 300 each of three, four and five stars). About 260k input and 900 output tokens per run, roughly $0.55 each; nothing saved.

- **Somewhat more consistent, still not stable.** About eight of twelve match. Three dish themes (wood-fired oysters, fried alligator, Louisiana cochon entree) appear in both runs under the "large share of reviews" rule, as do server attitude, pacing, host and reservations (split in one run, merged in the other), price and portion value, and atmosphere (with drinks merged in one run, separate in the other).
- **Same remaining problems.** Both runs again have a residual "Other ..." dish bucket and partition the remaining food differently. "Hype and expectations", which appeared in every v1 run, is missing from both. Run 22 adds "Check splitting and billing".
- **The runs are not independent at this size.** The one- and two-star reviews are identical in both samples, and the three-star samples overlap heavily, so agreement is inflated.
- **Conclusion:** a larger sample is not the main lever. The variation comes from how the model partitions dishes and merges or splits service topics, which is a prompt question.

### v3 (2026-10-16)

Changes from v2:

- Added: each theme must be a single topic that fits in a short name. Don't join unrelated topics with "and", and don't make leftover buckets ("Other dishes", "Miscellaneous"). Items that fit no coherent group are left out and fall to the `other` system theme.
- Enforced in code, not just in the prompt: `DiscoveryResult` rejects names containing the whole word other, others, misc, or miscellaneous (the retry sends the error back). A name like "Mother sauces" passes.

Results (Cochon, entity 11, 120 reviews per rating, runs 27 and 28; nothing saved). Run 27 needed the retry (237k input tokens in total, so the validator caught a bad first answer); run 28 passed first time (119k).

- **The ban worked mechanically.** No "Other ..." buckets in either run.
- **Fairly stable: roughly nine of twelve match**, with granularity differences. Both have server attentiveness, hostess and seating, seasoning, sides/bread/desserts, price, rabbit and dumplings, and atmosphere. Wait times and pacing are merged in one run and split in the other, and alligator and oysters are separate in one and joined in the other.
- **Side effect: dish themes multiplied.** The leftover bucket was how the model grouped minor dishes. With it banned, run 27 spends six of twelve slots on individual dishes (alligator, oysters, signature cochon, pork cheeks and ribs, rabbit and dumplings, sides), and run 28 about four. "Large share of reviews" is not constraining the model.
- **Breadth got worse.** Drinks and bar, hype and expectations, and cleanliness are missing from both runs (drinks was in both v2 runs).
- **Single-topic rule only partly followed.** Run 28 still joins "Alligator and oysters" as "the two most commonly ordered starters".

### v4 (2026-10-16)

Changes from v3:

- The theme count is loose, 5 to 15 (was 8 to 12), and the reviews decide it. The prompt now says to identify themes that recur in a significant share of reviews, that the goal is the ones that matter and not the most themes, and that a topic raised by only a few reviews is not a theme. `MIN_THEMES` and `MAX_THEMES` in `discover_themes.py` set the limits.

Results (Cochon, entity 11, 120 reviews per rating, runs 32 and 33, about 118k and 122k input tokens, nothing saved):

- **Both runs returned exactly 15 themes, the cap.** The model treated the upper limit as a target. "Significant share" gives it nothing to measure against, and it can't count how many reviews raise a topic.
- **Low-frequency themes are likely in the list.** Run 32 includes "Dietary accommodations" and "Cleanliness and hygiene", run 33 five single-dish themes (alligator, oysters, signature cochon, rabbit and dumplings, pork cheeks and charcuterie). Whether they clear a significant share can only be checked by classification.
- **Breadth is back:** drinks and hype and expectations appear in both runs.
- **Run 32 grouped dishes by course** (appetizers, entrees, sides/bread/desserts), the better pattern. Run 33 enumerated dishes.
- About nine of fifteen match across the runs.

### v5 (2026-10-16)

Changes from v4:

- The prompt gives the threshold as a number: a theme must be raised by roughly 5% or more of the sampled reviews (about 30 of 600), the reviews decide the count between 5 and 15, and fewer than 15 is the expected result. `MIN_THEME_SHARE` sets the percentage and `build_system_prompt` takes the sample size. The 5% applies to the stratified sample, which over-represents low ratings, so it is not exactly 5% of all reviews.

Results (Cochon, entity 11, 120 reviews per rating; nothing saved):

- **Run 37 succeeded with 15 themes** (about 116k input tokens): service quality, host stand and seating, reservations and wait times, pacing and kitchen timing, fried alligator, oysters, Louisiana Cochon signature dish, rabbit and dumplings, pork and meat dishes, saltiness and seasoning, sides and bread, price and portion size, drinks and bar, atmosphere and noise, hype and expectations.
- **Run 38 failed validation twice** (about 246k input tokens in total): both answers contained a theme name with the word "other" (the residual-bucket rule, which is enforced in code). The failed run is recorded in `analysis_runs`. So the second sample for a stability comparison is missing.
- **15 may be about right for this restaurant, not just the cap.** A rough keyword count over all 4,480 Cochon reviews gives these shares: server/service 46%, oyster 32%, alligator 30%, wait 24%, drinks/bar 21%, dessert/cake/pie 21%, rabbit 18%, ribs 18%, reservation 17%, mac and cheese 13%, rolls/bread 12%, price 12%, pork cheek 11%, gumbo 9%, cocktail 9%, boudin 9%, salt 7%, portion 6%, hype/overrated 4%, cleanliness 3%, noise 3%, dietary/allergy 2%. Single dishes like oysters and alligator genuinely clear 5%. The 5% threshold also matches what v5 dropped: dietary needs and cleanliness, which v4 listed, are under 5% and are gone. Hype (4%) is borderline.
- **The number of themes is not settled.** Dishes above 5% that the run folded together or left out (gumbo, boudin, desserts) show the 15 cap can force grouping. This is a rough keyword check, so the real shares come from classification (roadmap step 6).
- Retry behavior: a run now fails entirely if two attempts in a row break the residual-name rule.

### v6 (2026-10-16)

Changes from v5:

- **Hard floor on the threshold.** A theme must be raised by at least 5% of the sampled reviews and never by fewer than 10 (`MIN_THEME_SHARE`, `MIN_THEME_REVIEWS`; `min_theme_reviews(sample_size)` returns the larger). The prompt states the resulting count and percentage, for example "at least 30 of the 600 reviews below (5% of them)" or "at least 10 of the 100 reviews below (10% of them)". Ten is a judgment call: below roughly ten mentions, a topic is hard to tell apart from coincidence or one writer repeating a point. Tune it with evidence from classification.
- **Minimum sample.** Discovery fails, without calling the API, if fewer than `MIN_THEMES * MIN_THEME_REVIEWS` = 50 reviews are sampled, because five themes at ten mentions each would be impossible. The failure is recorded on the run.

For the 600-review samples used so far the prompt is the same as v5 apart from wording (30 reviews, 5%), so no new real run was made. The behavior was checked with the stub client.

## Candidate themes replace `other` (2026-10-26)

Direction change. The single `other` theme per entity is gone. The classifier now creates a **candidate** theme (a `themes` row) for each specific point no established theme covers, and a later consolidation step merges overlapping candidates. This supersedes the `other` and `theme_title` design and the rediscovery described above (`rediscover_themes.py`, now deleted).

Why: a review could have only one `other` row, so a second uncovered point was lost or crammed into one title; the 818 free-text titles needed clustering anyway; and adding a rediscovered theme meant reclassifying the `other` reviews. With candidates, every point is its own countable theme from the start, and promoting one needs no reclassification.

- **Schema** (migration `de22bf45d938`): `kind` is discovered, candidate, or general. `themes` gains `merged_into_id` (self-reference) and `merged_in_run_id` (the consolidation run). There is one `general` theme per entity (partial unique index) and theme names are unique per entity, case-insensitively. `review_themes.theme_title` is dropped.
- **Data conversion** (same migration, rehearsed in a rolled-back transaction first, with a check that no review lost its tags): each distinct normalized `other` title became a candidate theme named after its most common spelling, and each `other` tag became a tag on it. 817 candidates were created from 818 titles (one title matched the existing "Rabbit and dumplings" theme, so its tag went there). All 18,898 tags and 4,480 tagged reviews were preserved. The `other` themes and tags were deleted. The migration cannot be reversed.
- **Classifier (prompt v4).** The prompt lists established themes first, then candidates that at least 10 reviews raised (23 for Cochon), then `general`. Each assignment is a listed theme ID (an enum) or null plus a short title. Code matches titles to existing themes by normalized name (lower case, spacing, a plural s) and otherwise creates a candidate, one title at a time on the single writing session, backed by the unique index. Merged candidates are not shown, and their names resolve to the theme they merged into. A dry run with `--include-processed` on 100 reviews showed candidates being reused and new ones appearing.
- **Consolidation** (`consolidate_themes.py`, prompt v1 and v2). The model sees all candidates with review counts (817 for Cochon, 17k input tokens, 12k to 15k output tokens, roughly $0.2 per run on Sonnet) and returns groups of overlapping candidates plus descriptions for large ungrouped ones. Merging sets `merged_into_id` on the non-canonical members; nothing is retagged. A group named like a candidate that isn't among its members takes that candidate in. Promotion makes a candidate `discovered` when 2% of classified reviews (90 of 4,480) raise it, counting merged members, and only if it has a real description. `--undo` clears a run's merges, exactly when it is the latest consolidation.
- **First dry runs on Cochon** (runs 118 and 119; nothing applied): 817 candidates became 44 to 46 groups. The groups were sensible: Desserts (about 243 reviews, with spelling variants like "Desert"), Fish (about 197), Catfish (about 107), Salads (about 101), Braised beef (about 87), Shrimp (about 83), Crawfish pie (about 60), Crab dishes (about 41), Location and parking, Menu variety, Splitting checks, and many smaller ones. Four would pass the promotion threshold. v2 added a rule against overlapping groups; it did not merge Catfish into Fish, since catfish courtbouillon is raised by about 2% of reviews on its own.
- **Known limitations.** Merged counts are distinct reviews through the mapping. A review tagged with several merged candidates gets one combined sentiment for the merged theme: positive together with negative (or any mixed) is mixed, and neutral gives way to positive or negative (`theme_stats.py`, added after the first dry runs). The candidate list shown to the classifier is a snapshot per run, so the same new point can get slightly different titles within one run; consolidation cleans those up. Undo is exact only for the latest consolidation, because a later run can re-point earlier merges. The model-written group names can include words like "entrees" that individual reviews don't use; that matters less at theme level.

### Consolidation applied to Cochon (run 119, 2026-10-26)

Applied from the dry-run report, with no further API cost. 705 candidates were merged into 46 groups (26 new canonical candidates, 20 descriptions written for large ungrouped ones), and four were promoted to established themes. No tags moved (18,898 tags, 4,480 tagged reviews before and after), every merge points at a root, and Cochon now has 19 established themes, 839 candidates (705 of them merged), and `general`.

| New theme | Reviews | Positive | Negative | Neutral | Mixed |
|---|---|---|---|---|---|
| Desserts | 242 | 202 | 24 | 10 | 6 |
| Fish | 197 | 149 | 25 | 17 | 6 |
| Salads | 101 | 81 | 14 | 4 | 2 |
| Catfish | 107 | 78 | 16 | 10 | 3 |

- The classifier now sees 19 established themes and 18 candidates (those raised by at least 10 reviews after merging), the largest being Brisket (89), Braised beef (87), Shrimp (83), Crawfish pie (60), Offal and head cheese (58) and Sandwiches (56). Brisket and Braised beef are just under the 90-review promotion threshold.
- **Open design problem: the trigger.** 32.9% of classified reviews still carry a candidate tag, because the long tail of small candidates remains. A trigger of "15% of reviews carry a candidate tag" will therefore keep firing after every consolidation. A better trigger measures what is new since the last consolidation (unmerged candidates created after it, or the share of reviews classified since). To decide.
- "Fish" and "Catfish" overlap as themes; "Catfish" stays separate because it is raised by about 2% of reviews alone.

### Candidate descriptions (2026-10-26)

Candidates were created with a placeholder description ("Candidate theme from an uncovered point titled ..."), and only promoted or grouped ones got a real one. `describe_themes.py` now writes a one or two sentence description, in the style of the established themes, for every candidate that still has a placeholder, merged ones included.

- **Input is only names.** The model sees the candidate's name and the names merged into it (up to 12), plus three established descriptions as style examples, and is told not to add facts or items. No review text is shown. Haiku 5.5, 60 candidates per request, low effort.
- **When it runs.** At the end of every classification run (for the candidates the run created; a failure is a warning on the run, not a failure), and in consolidation after merging and before promotion, since a candidate with a placeholder is not promoted.
- **Backfill on Cochon:** 797 candidates described in one pass, 0 placeholders left, 22k input and 37k output tokens (a few cents). Descriptions average 95 characters.
- **Quality.** Mostly accurate and in the right style, but shorter and plainer than discovered ones, and some pad with generic phrases ("including its preparation, taste, or texture") or merge unrelated members ("Pasta dishes such as pasta with chicken and cheese macaroni"). Showing a few review snippets per candidate would likely sharpen them; not done because the names alone are enough to define scope.

### Descriptions rewritten on promotion (2026-10-26)

A promoted theme kept the description it had as a candidate: a Sonnet-written group description from consolidation, based on candidate names and not on what reviews say, with no scope boundary against neighbouring themes. Established themes' descriptions are what the classifier uses to decide what belongs, so they matter more than a candidate's.

`describe_themes.refine_description` now rewrites the description at promotion (and on demand with `--refine NAME ...`). It shows Sonnet the theme's name and merged member names, the names and descriptions of the other established themes, and 40 random reviews that raised the theme (the candidate or anything merged into it), each cut to about 500 characters around the first mention of a keyword from the names. It asks for one or two sentences that define what belongs, name the specific items found in the reviews, and say how the theme differs where it could be confused with a neighbour. All promotions are saved before any rewrite, so new themes appear among each other's neighbours. A failed or unusable rewrite (empty, a placeholder, over 500 characters) keeps the old description.

Applied to Cochon's four promoted themes (30k input, under 1k output tokens, a few cents). Example, Fish: before, "Fish entrees and specials such as redfish, gulf fish, fisherman-style fish, snapper, drum, grouper, trout and whole fish." After: "Reviews of the non-catfish fish entrees at Cochon, such as the oven-roasted Gulf fish 'fisherman's style,' redfish, amberjack, red snapper, drum, and the catch of the day or fish special, whether praised or criticized for freshness, cooking, flavor, bland or muddy taste, price, or being served without sides. Excludes catfish dishes and courtbouillon, which are their own theme, and excludes oysters, alligator, and other seafood or non-fish dishes." Catfish now says it excludes the other fish entrees, and Desserts and Salads each name what they exclude (savory dishes and sides, cooked sides and potato salad).

- **Longer than the discovered themes' descriptions** (about 400 to 500 characters against 86 to 138), because they carry boundaries and named items. That makes the classifier prompt somewhat longer.
- **Entity name in the text.** The rewrites say "at Cochon". That is harmless inside one entity's themes.
- **Existing neighbours are not updated.** The discovered themes from run 46 do not mention the new themes. For example "Sides, bread and small plates" still lists nothing about salads. Consider refining them as well (`--refine` with their names), or at least when a theme overlaps a newly promoted one.

### Descriptions say only what a theme covers (2026-10-26, reverses part of the entry above)

The first rewrite told the model to say how a theme differs from its neighbours, which produced "Excludes catfish dishes ..." sentences. Descriptions now describe only the topics the theme covers.

- **Prompt.** It no longer asks for boundaries. It says to describe only what the theme covers, never to say what it excludes or refer to other themes, and to leave out any item that belongs to one of the neighbouring themes (the neighbours are still shown, so Fish no longer lists catfish).
- **Check in code.** The first run of the new prompt still produced "non-catfish fish entrees", so a rewrite that contains exclude/excluding/except/other than/apart from/separate from/rather than/not including/non-... is rejected. The retry says which phrase to drop, and if the second answer fails too, the old description is kept. The pattern is deliberately blunt: it also rejects harmless uses such as "non-stop", which only costs a retry.
- **Applied to Cochon** (Desserts, Fish, Catfish, Salads, about 55k input tokens in all, a few cents). Fish now reads: "The whole-fish and fillet entrees at Cochon, chiefly the oven- or wood-fire-roasted Gulf fish served 'fisherman's style' (skin-on fillet), plus redfish, red snapper, drum, amberjack and the daily catch or fish special, whether blackened, baked, grilled or crispy. Comments praise or criticize how freshly and perfectly it was cooked, its tenderness, moistness, crispy skin, seasoning or blandness, saltiness, muddy or fishy taste, portion, and whether it is worth the price." Catfish, Desserts and Salads read the same way, listing items and what reviewers comment on.
- **Trade-off.** Without stated boundaries, the overlap between Fish and Catfish is handled only by which items each description lists. The classifier can still tag a review with both.

## Trigger thresholds (2026-10-26)

The first trigger ("15% of classified reviews carry a candidate tag") was wrong in practice. After the first consolidation of Cochon, 32.9% of reviews still carried a candidate tag, because the long tail of small, unmerged candidates stays, so it would have fired after every classification run. A share of tagged reviews cannot go down once the work is done.

Two separate triggers replace it (`consolidate_themes.py`, `trigger_status`; `--status` prints them without changing anything):

- **Consolidation: new candidates since the last applied consolidation.** Fire when at least `max(15, 1% of classified reviews)` unmerged candidates were created by runs that started after the last applied consolidation finished (45 for Cochon). This counts work that has not been looked at, so it is zero right after a consolidation and grows only as classification creates new titles. A grouping call costs about the same (roughly $0.2 on Cochon, 17k input tokens) however many candidates are new, so waiting for a batch is cheaper than running after every upload; the floor of 15 lets a small review set consolidate at all. Candidates a consolidation creates for itself are not new.
- **Promotion: any unmerged candidate that passes the promotion threshold.** Fire when a root candidate (counting merged members) is raised by 2% of classified reviews, at least 10 (90 for Cochon). Deciding this takes a count, no model call, so it can be checked after every classification run; only the description rewrite costs anything (`--promote-only`). Cochon has none today: the nearest, Brisket, has 89 reviews against 90.

How "last applied consolidation" is found: the run's `error` note. A proposal is saved as "Consolidation proposed, not applied." (a dry run, or a run nobody applied), and applying it changes the note to "Consolidation applied." The notes sit in `analysis_runs.error`, which already holds warnings on succeeded runs. Run 119 was marked applied by hand, so Cochon now reads "0 new candidates since run 119".

Not decided: the numbers 1%, 15, 2%, and 10 are judgment calls to tune against more entities. A consolidation that finds nothing to merge still counts as applied once applied, so it will not fire again until more candidates arrive. A time-based trigger ("at least a week old") was left out because a count is enough for now.

### Promotion threshold changed to 3% (at least 25) (2026-10-26)

The promotion threshold, previously 2% of classified reviews with a floor of 10 (90 reviews for Cochon), is now 3% with a floor of 25 (`PROMOTE_MIN_SHARE`, `PROMOTE_MIN_REVIEWS`; 134 reviews for Cochon). The same share-plus-floor form as discovery's rule: a share for large review sets, and a minimum count so a small set can't promote a topic only a handful of reviews mention.

- **Effect on Cochon.** Nothing is demoted, since themes are never retired. Catfish (107 reviews) and Salads (101), promoted at the old threshold, would not qualify now, while Desserts (242) and Fish (197) still would. The nearest unpromoted candidates (Brisket 89, Braised beef 87, Shrimp 83) are further from the line than before.
- **Consolidation prompt.** It tells the model which candidates can become established themes using the same number, so it now asks for a standalone description only for candidates of 134 reviews or more.
- Earlier entries above describe the old 2% rule and its numbers.

### Consolidation after every batch: considered, not adopted (2026-10-26)

Kept the trigger above (at least max(15, 1% of classified reviews) new candidates since the last applied consolidation). Running after every batch would not be expensive, but it would not be stable either.

- **Measured cost per run** (Sonnet 5.5): $0.16 to $0.18 for the first consolidation (817 candidates, 17k input and 12k to 15k output tokens), and $0.07 in the steady state (134 unmerged candidates, run 161: 5.5k input and 6.1k output). Output tokens are about 85% of it. The cost grows with the number of unmerged candidates shown, up to about $0.30 at the cap of 1,500. Classification costs about $0.16 per 1,000 reviews, so a per-batch consolidation would add roughly 10 to 100% depending on batch size.
- **Why not.** Run 161 (forced, dry run, nothing applied) proposed 29 groups with no new candidates, merging existing roots into broader ones ("Shellfish dishes" 200 reviews from shrimp, crab, crawfish pie and mussels; "Beef dishes" 190 from brisket and braised beef). Each additional run can keep coarsening the themes. The trigger is there to prevent that churn, not only to save money.
- **Not built:** an option to show the model only the new candidates plus a short list of existing roots to match them against, so a run groups new titles without re-merging old groups. Revisit if forcing runs turns out to be wanted.

## Authentication and the Data API (2026-10-27)

- **The Data API was exposing the whole database.** Supabase puts every table in the `public` schema behind PostgREST and grants the `anon` and `authenticated` roles full access to new tables. The project's `/rest/v1/` endpoint was live, and a query as the `anon` role returned all 56,901 reviews. With RLS off, anyone holding the project's public anon key could have read, changed, or deleted everything, including users' email addresses. Migration `415000255ba0` enables RLS on all eight tables with no policies and revokes both roles' privileges. Afterwards, the same queries as `anon` and `authenticated` fail with "permission denied", and the backend is unaffected (its `postgres` role has BYPASSRLS, which `check_rls.py` verifies). A policy-free RLS setup means the Data API can never be used by accident; the alternative, switching the Data API off in the dashboard, is still advisable and independent of this. Every future table needs the same treatment.
- **Token verification uses the project's public keys only.** The project's JWKS (`/auth/v1/.well-known/jwks.json`) publishes one ES256 key, so tokens are verified with it and no JWT secret is needed or accepted. HS256 and unsigned tokens are rejected outright, which closes the algorithm-confusion attack. Required claims are expiry, subject, audience (`authenticated`) and issuer, with 10 seconds of clock leeway. The key fetch is blocking, so it runs in a thread, and keys are cached for an hour (PyJWT also refetches when a key ID is unknown, which handles key rotation).
- **Users.** The row is created on first sight with the Supabase user ID, and its email follows later changes in Supabase. A different account claiming an email already stored is refused with a 409 rather than silently merged. Tokens without an email (anonymous sign-ins) are rejected because `users.email` is required.
- **Entity access.** Not visible means 404, so IDs can't be probed. A public entity the user can see but doesn't own is a 403 on writes, because it is not hidden.
- **Tested** with tokens signed by a generated key (valid, expired, within leeway, wrong audience, wrong issuer, missing claims, another key's signature, HS256, unsigned, garbage, unreachable keys) and over HTTP with a rolled-back database (first-sight creation, no duplicates, email change, email clash, every access case). It has not been tried against a real Supabase sign-in: that needs a user and a frontend or a password-grant call with the anon key.
- Also added: `requirements.txt` (direct dependencies pinned), and the unauthenticated `/upload` and `/businesses` endpoints were removed from `main.py` since they could not run any more.

