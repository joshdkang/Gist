# Gist

A general-purpose review insight engine. A user creates an entity (a restaurant, product, app, or anything else that gets reviewed), uploads reviews for it, and Gist turns them into specific, recurring themes with sentiment, frequency, example quotes, and trends over time. This is a portfolio project, so clear code and documented decisions matter as much as features.

The project started as a Yelp restaurant tool and is being generalized. Yelp is the first dataset, not the target domain. Nothing after ingestion should assume restaurants, Yelp, or star ratings.

## User flow
1. The user signs in and creates an entity, which they own.
2. The user uploads a file of reviews for that entity. Each upload is recorded as an import.
3. The upload queues an analysis job. The model discovers themes (first upload) or classifies the new reviews against existing themes (later uploads).
4. The user views the results: themes, sentiment, frequency, example quotes, and trends.

## Stack
- Python 3.14, FastAPI, SQLAlchemy 2.0 (async, asyncpg), Pydantic and pydantic-settings
- Supabase: Postgres, plus Supabase Auth for user accounts. pgvector is available for later.
- Claude API (`anthropic` SDK), using forced tool use for structured output validated by Pydantic
- Alembic for migrations (`src/alembic/`, async `env.py`; run `alembic` from `src/`)
- Frontend: Next.js with TypeScript (not started)

## Environment
- Windows, PowerShell, VS Code with the Claude Code extension. The workspace is the repo root (`Gist/`).
- VS Code's interpreter must be `src/.venv/Scripts/python.exe`, set in `.vscode/settings.json` via `python.defaultInterpreterPath`.
- Backend code lives in `src/`. The venv is `src/.venv` (Python 3.14). Run commands from `src/`.
- Activate the venv with `.venv\Scripts\activate`.
- An older `.venv` at the repo root uses Python 3.9. Don't use it, because it breaks `X | None` type syntax. Delete it if it's still there.
- Config lives in `.env` at the repo root and is loaded by `config.py`. `DATABASE_URL` must use the `postgresql+asyncpg://` scheme. `ANTHROPIC_API_KEY` is also read from there. Never commit `.env`.
- Run the API with `uvicorn main:app --reload` from `src/`.

## Current files (`src/`)
Files marked "pre-generalization" still use the old `Business` and `Feedback` models, which no longer exist, so they won't import until they're ported (roadmap steps 4 and 5).

- `config.py`: `Settings` via pydantic-settings (`SettingsConfigDict`, with `env_file` pointing at the repo-root `.env`)
- `database.py`: async engine, `async_session_maker`, `Base(DeclarativeBase)`, and the `get_db` dependency
- `models.py`: the target schema below, as SQLAlchemy models
- `alembic/`: migrations. The initial migration creates the target schema and copies the old `businesses`/`feedback` rows into public entities, imports, and reviews before dropping the old tables.
- `entities.py`: `create_entity`, which creates an entity together with its `other` and `general` system themes
- `ingestion.py`: generic ingestion. `ColumnMapping`, rating scales, `content_hash`, dedupe, and the import record. `ingest_rows` takes any iterable of row dicts and `ingest_csv` takes a text-mode CSV file. It has no knowledge of users or HTTP.
- `main.py` (pre-generalization): FastAPI app. The lifespan creates tables. Endpoints: `/upload` (CSV) and `/businesses`
- `load_yelp.py`: script that loads the top 10 Yelp restaurants as public entities through `ingestion.py`. Re-running it is safe (existing entities are reused and duplicate reviews are skipped).
- `discover_themes.py` (pre-generalization): theme discovery for one business, with a hardcoded restaurant category enum (to be generalized: remove categories, parameterize the prompt)

## Target schema
Ownership chain: user → entity → imports and reviews → analysis results. Users can read entities they own or public ones, and can only write to (upload, analyze, edit, delete) entities they own.

- `users`: id (UUID, the same ID as the Supabase Auth user), email, display_name, created_at
- `entities`: id, owner_id (FK users, nullable), is_public (default false), name, description (nullable), metadata (JSONB), created_at. The thing being reviewed. `description` is optional context for the prompt, such as "a Cajun restaurant in New Orleans" or "a budgeting app for iOS." Public sample entities have `is_public = true` and no owner.
- `imports`: id, entity_id, filename, source, status, rows_received, rows_inserted, rows_skipped, error, created_at. One row per upload. Later uploads add reviews to the same entity, which is what incremental analysis picks up.
- `reviews`: id, entity_id, import_id, external_id (nullable), content_hash, text, title (nullable), rating (numeric, nullable), rating_scale (nullable), review_date (nullable), metadata (JSONB). Unique on (entity_id, content_hash) so re-uploading the same file doesn't duplicate reviews. `content_hash` is a hash of the normalized text plus review date, because many uploaded files have no review IDs.
- `themes`: id, entity_id, kind (discovered, other, or general), name, description, discovered_in_run_id (FK analysis_runs, null for system themes). Themes persist across rediscoveries, and all of an entity's themes are in use. Each entity has exactly one `other` and one `general` theme, created by code, not the model, when the entity is created.
- `review_themes`: review_id, theme_id, sentiment (positive, negative, mixed, or neutral; null only for code fallbacks), run_id. Primary key is (review_id, theme_id). A review can belong to several themes. Every classified review gets at least one row, so "unprocessed" means "no rows here."
- `analysis_runs`: id, entity_id, requested_by (FK users, nullable for script runs on public entities), type (discover or classify), status (queued, running, succeeded, failed), model, reviews_processed, input_tokens, output_tokens, error, created_at, started_at, completed_at. Doubles as the job queue and the cost log.

The existing data can be reloaded from source files, so dropping the old tables and recreating them is fine. Use Alembic for this and for every schema change after it.

## Conventions
- SQLAlchemy 2.0 typed style: `Mapped[...]` and `mapped_column`, with `X | None` for nullable columns.
- Bulk inserts use Core `insert(Model)` with a list of dicts, in batches.
- Async throughout: `AsyncSession` and asyncpg. Use a lifespan context manager, not `on_event`.
- Use descriptive variable names. Single letters are fine only for conventional cases like a file handle `f`.
- Structured LLM output comes from forced tool use plus Pydantic `model_validate`. Don't parse free-form JSON from model text.
- Every endpoint that touches user data depends on `get_current_user`. Read endpoints allow entities the user owns or that are public. Write endpoints require ownership. Return 404, not 403, for resources the user can't see, so IDs can't be probed. Put these checks in shared helpers (`get_readable_entity` and `get_owned_entity`) rather than repeating them per endpoint.

## Design decisions
- **Authentication.** Supabase Auth handles sign-up, login, and password storage. Don't build password handling. The frontend signs in with Supabase and sends the access token. FastAPI verifies the JWT in a `get_current_user` dependency and creates the `users` row on first sight.
- **Authorization lives in the API.** The backend connects to Postgres as a privileged role, which bypasses Row Level Security, so ownership checks must happen in FastAPI. Keep the Supabase Data API disabled, or enable RLS on every table, so tables aren't reachable directly with the public anon key.
- **Public sample data.** The Yelp restaurants are public sample entities (`is_public = true`, no owner) that every signed-in user can browse, so a new visitor sees real results immediately without uploading anything. They're loaded and analyzed by scripts. Users can't upload to them or trigger analysis on them, so visitors don't cost API money.
- **No taxonomies or categories.** Themes are discovered freely per entity. Domain context for the prompt comes from the entity's name and optional description, not a predefined category list.
- **Domain-agnostic.** Source-specific logic lives only in the ingestion layer and the scripts that feed it. Prompts are parameterized by the entity, never hardcoded to restaurants.
- **Ratings are optional.** Nothing in the analysis may depend on a rating. Sentiment comes from classification, not stars. When ratings exist, they're used to stratify the discovery sample and can be shown alongside results.
- **Ingestion.** There is one ingestion path: a generic CSV ingestion with a column mapping, which normalizes rows, computes `content_hash`, skips duplicates, and records an import. The Yelp restaurants go through it too: `load_yelp.py` is a script that maps the Yelp JSON fields onto the same column names, creates the public entity, and calls the same ingestion code, so no separate Yelp adapter or adapter interface is needed. Add an adapter interface only if a second input format (not just different column names) turns up. One file uploads reviews for one entity.
- **Upload limits.** Cap upload size and row count per upload. Large public datasets like Yelp are loaded with scripts, not through the API.
- **Two-pass analysis.** Discovery runs on a sample and produces 8-12 themes. Classification then tags every review against the entity's themes plus the two system themes, so theme names stay consistent and counts add up.
- **Theme conventions.** These follow aspect-based sentiment analysis, where the topic (aspect) and the sentiment are kept separate.
  - **Sentiment-neutral names and descriptions.** A theme names a topic, not an opinion: "Service speed," not "Slow service." Sentiment lives only in `review_themes.sentiment`. Every theme must be able to hold both positive and negative reviews. This prevents opposing pairs like "slow service" and "fast service," and it lets a trend show sentiment improving within one theme instead of one theme shrinking while another grows.
  - **Specific topics with non-overlapping scope.** Themes should be as broad as needed to avoid overlap, but not so broad they stop being actionable. "Gumbo" or "Brunch menu" is right. "Food" is too broad, and "Great gumbo" is opinionated. Each description defines the theme's scope clearly enough that a reader could decide whether a review belongs.
  - **Prefer existing themes.** Classification assigns a review to an existing theme whenever its topic reasonably falls within that theme's description, whatever its sentiment. `other` is only for specific feedback that no theme's scope covers, and `general` is only for reviews with no discernible topic. Don't stretch a theme to absorb unrelated reviews, though: that dilutes the theme's meaning and hides the rediscovery signal that `other` exists to provide.
- **Every review gets at least one theme.** Two system themes exist alongside the discovered ones. `other` is for reviews with specific feedback that no discovered theme covers ("the parking lot was flooded"). `general` is for reviews with no discernible topic ("Great!", "meh"). They're kept separate because a rising `other` share means the themes are going stale, while `general` reviews are just low-information. The classification schema requires at least one assignment per review (`minItems: 1`), and theme IDs are an enum of the entity's themes. Very short or letterless reviews skip the model and go straight to `general`. If a review still comes back unassigned or invalid after one retry, code assigns it to `general` with a null sentiment and logs it on the run. Theme rankings exclude `general` and report its share separately, and the rediscovery trigger looks only at the `other` share.
- **Discovery sampling.** With ratings, take an equal random sample per rating level (about 120 each). Without ratings, sample randomly, or stratify by date. Verify sample size with a stability check: run discovery on two independent samples and compare the theme lists.
- **Analysis runs as background jobs.** An upload creates a `queued` row in `analysis_runs`: discover if the entity has no themes yet, otherwise classify the new reviews. A worker process claims jobs with `SELECT ... FOR UPDATE SKIP LOCKED`, runs them, and records status, tokens, and errors. The API only reads results and job status, so requests never wait on the LLM.
- **Incremental refresh.** New uploads are classified against the entity's themes.
- **Incremental rediscovery.** Themes persist and accumulate, so trend lines continue across rediscoveries. Rediscovery runs when the `other` share passes a threshold or the user asks. It samples mostly from reviews tagged `other`, gives the model the existing themes, and asks only for new themes the existing ones don't cover. After new themes are added, reviews currently tagged `other` are reclassified against them, so new themes don't appear to start on the day they were discovered.
- **No theme merging for now.** Duplicate themes are prevented rather than repaired: sentiment-neutral names remove opposing pairs, and rediscovery sees the existing themes. Automated merging was considered and deferred, because a wrong merge loses information and is hard to undo, while a leftover duplicate is only untidy. Check for duplicates during the eval pass. If they turn out to be a real problem, add merging as a user-confirmed, reversible action (record a mapping instead of moving and deleting `review_themes` rows), with a `merged_into_id` column added by migration at that point.
- **Quiet themes.** Themes are never retired automatically. The UI ranks themes by mention volume in a recent window and collapses themes with no recent mentions, so the data stays complete while the display stays current.
- **Cost controls.** Every user's analysis costs real API money. Enforce per-user limits (reviews per upload, entities per user, analysis runs per day), and use `analysis_runs` token counts to track spend per user.
- **Models and cost.** Sonnet for discovery. Test a cheaper model for classification. Use the Batch API for large runs. Iterate on prompts with small samples.
- **Primary user.** Anyone who receives reviews (product teams, app developers, small businesses) and wants to know what customers keep saying and how it changes over time.
- **Comparison is deferred, and user-driven when it comes.** Users will pick which of their entities to compare. Because each entity's themes are discovered independently, comparison will need a shared theme set: run a joint discovery on a combined sample from the selected entities, then classify each entity's reviews against the shared themes. Nothing in the current schema blocks this.
- **Deployment.** Docker for packaging (Dockerfiles for the API and the worker, docker-compose for local development), a managed AWS container service (App Runner or ECS Fargate), and GitHub Actions CI to run tests and build images. No Kubernetes.

## Open decisions
- Which second dataset to add to prove the engine generalizes (it should differ sharply from restaurants, such as product or app store reviews).

## Roadmap
1. Set up Alembic and migrate to the target schema. (Done: the baseline migration is applied.)
2. Add Supabase Auth: JWT verification in a `get_current_user` dependency, and the ownership helpers. Confirm the Supabase Data API is disabled or RLS is on.
3. Build the CSV ingestion: column mapping, normalization, `content_hash`, dedupe, and an import record. Rework `load_yelp.py` to use it, and reload the Yelp restaurants as public sample entities (each created with its `other` and `general` themes). (Done: 10 public entities and 56,901 reviews loaded, none skipped.)
4. Add entity endpoints (create, list, get, delete) and an upload endpoint with size limits that records an import.
5. Generalize `discover_themes.py`: remove categories, parameterize the prompt by entity name and description, add the sampling fallback when there are no ratings. Run it on the restaurant with about 4,400 reviews, check the themes by hand, and run the stability check.
6. Build the classification pass and the job worker.
7. Upload the second dataset through the normal user flow and run the full pipeline on it to prove the engine generalizes.
8. Add result endpoints for the frontend (themes, counts, sentiment, trends, example reviews, job status).
9. Build the Next.js frontend: sign-in, entity list, create entity, upload flow, theme cards, trend chart, drill-down into reviews.
10. Add Dockerfiles and docker-compose.
11. Write up an eval comparing model themes to hand labels. Try v2 clustering with embeddings in pgvector.
12. Deploy to AWS with CI.

## Working notes
- Record each prompt version, what was wrong with its output, and what changed in `docs/decisions.md`. Record major direction changes there too (generalizing beyond restaurants, adding users, removing taxonomies).
