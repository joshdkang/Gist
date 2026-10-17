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
