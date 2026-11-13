# Gist frontend

Next.js (App Router, TypeScript, Tailwind) for the Gist API in `../src`.

## Run it

```sh
cp .env.example .env.local   # then fill in the Supabase project URL and publishable key
npm install
npm run dev                  # http://localhost:3000
```

The API must be running too (`uvicorn main:app --reload` from `../src`), and its `CORS_ORIGINS` must include the
frontend's origin (the default allows `http://localhost:3000`).

## How it fits together

- **Sign-in** is Supabase Auth (`@supabase/ssr`). The session lives in cookies; `src/middleware.ts` refreshes it
  and sends signed-out visitors to `/login`. That only decides which pages show: the API checks the token on
  every request.
- **Data** comes from the API in the browser (`src/lib/api.ts`), which sends the Supabase access token as a
  bearer token and surfaces the API's error messages as written. TanStack Query caches it; an entity's page
  polls every 3 seconds while an analysis is queued or running and refreshes the results when it ends.
- **Pages**: `/` (your entities and the public samples), `/entities/new`, `/entities/[id]` (ratings,
  themes and emerging topics, or ratings by theme in their place, run status, and the owner's upload, edit and delete), `/entities/[id]/changes`
  (period comparison: each theme's share of one period's reviews against another, by default the one
  before, with the changes that aren't statistically significant set apart), `/entities/[id]/themes/[themeId]` (sentiment, trend by month, quarter or
  year as counts, a share of each period's reviews, or a sentiment mix, and the reviews), and
  `/entities/[id]/upload` (choose a CSV, map its columns, upload).
- **URLs carry context**: the entity page keeps its rating filter (`rating_scale`, `rating`) and period
  filter (`interval`, `period`) in the URL, and links to a theme carry them, so the theme's counts, trend and
  reviews cover the same ratings and its trend opens on the same periods. A theme's page
  has its own ratings card (the ratings of the reviews that raise it), whose bars set the same filter. A period
  comparison row opens the theme with `interval` and both periods (`period`) chosen in its trend.
- **Exports**: Export buttons on the theme list, ratings by theme, the period comparison and a theme's reviews
  download the API's CSV exports with the page's filters. A plain link can't send the sign-in token, so
  `downloadFile` in `src/lib/api.ts` fetches the file and hands it to the browser as a download.
- **Layouts** (`src/lib/layout.ts`): Desktop or Mobile, chosen in the account menu and kept in this browser
  only (a layout suits a device, unlike the palette, which follows the account). Until someone chooses, the
  screen width decides (Desktop from 1024px). Mobile is the narrow stacked page with theme cards. Desktop
  widens every page to 1360px (`pageWidth` in `components/ui.tsx`, which the header follows too) and, on
  the entity page, puts Ratings and Reviews over time side by side, adds a filter bar
  (`components/FilterBar.tsx`: the review count, a removable chip per filter, the view switch and the
  exports; it stays at the top of the window only while a filter is on) and shows the themes as a sortable
  table (`components/ThemeTable.tsx`), and ratings by theme as another (in `components/RatingDrivers.tsx`, with the
  themes whose ratings don't differ significantly behind a checkbox). Both use `components/SortableTable.tsx`. A theme's page on desktop puts its sentiment and
  trend across the page, then its ratings in a column that stays in view beside the reviews, whose active
  filters (periods, ratings) are removable chips beside the sentiment chips. The period comparison on desktop is one table
  (`components/ChangesTable.tsx`) with the change in mentions and in the negative share side by side; it lists
  the significant changes, with the rest behind a checkbox. The server renders Mobile, which the browser corrects.
- **All reviews** (`entities/[entityId]/reviews`): every review with its themes as tags, from the entity page's
  All reviews button (which carries its filters). The filter column (`src/components/ReviewFilterPanel.tsx`)
  has a search, the periods (`src/components/PeriodList.tsx`: a checklist with counts under the other
  filters and a Month/Quarter/Year switch; years are rows, or headings over a grid of their quarters or months;
  any mix can be ticked, and older years wait behind "Show earlier years"), the themes with counts from `reviews/facets` (ticking one reveals the sentiments of its
  mentions to choose; Any/All says how several combine; ↗ opens a theme's page on hover), and the ratings.
  Clicking a review's tag adds its theme to the filters. The filters live in the URL as the API's parameters
  (`theme=12:negative`, `match`, plus the shared ones; `src/lib/reviewFilters.ts`). On mobile the filter
  column opens from a Filters button.
- **Search** (`src/components/SearchBox.tsx`): a box in the entity page's filter bar (or above the theme list on
  mobile) and on a theme's reviews searches the review text; it runs on Enter. The search lives in the URL as
  `q`, the API's parameter, and is a filter like the others: a chip, part of Clear all, carried into a theme's
  page, the exports and the summary. Matched words are marked in the reviews (`Highlighted`), by a prefix of
  each word, since the API matched on word stems.
- **Reviews over time** (`src/components/ReviewsOverTime.tsx`): on the entity page when reviews have dates, the
  entity's reviews per month, quarter or year from `themes/periods`. Its bars choose a period filter, which
  lives in the URL as `interval` and repeated `period` (`src/lib/periodFilter.ts`), the parameters the API
  and the theme page's trend already use. It narrows the theme list like the rating filter, combines with
  it, and carries into a theme's page (its trend opens on those periods), the exports and the printable
  summary. Ratings by theme and the period comparison cover every review.
- **Printable summary** (`entities/[entityId]/summary`): the themes, the significant changes between two
  periods (picked on the page, or carried from the period comparison as `changes_interval`, `changes_period`
  and `changes_compare_to`) and the significant ratings by theme, laid out for paper and printed with the
  browser's own dialog (Save as PDF for a file). The rating and period filters carry over from the entity
  page and narrow the themes; a filter on one period also starts the comparison on that period. Print styles
  in `globals.css` keep the page white in dark mode and keep the sentiment colours of the user's palette; the
  app header is hidden when printing. The printed page has no top or bottom margin, so the browser can't add
  its own headers and footers (date, title, address); the report pads itself on every page instead
  (`.print-report`).
- **Upload** reads the CSV in the browser (Papa Parse) to count rows, guess the column mapping (the review
  text is the column with the longest values) and the rating scale, and preview the first rows. The API
  re-checks everything.
- **Colours** are tokens in `src/app/globals.css`, with a dark set. Sentiment: positive green, mixed
  yellow-orange, neutral light grey, negative red; every colour also has a text label. Colour-blind mode, a
  switch in the account menu under the email, swaps in positive blue, mixed bluish green, neutral grey
  and negative orange (`data-palette="colorblind"` on `<html>`). The choice is saved in the browser and
  in the Supabase user's metadata, so it follows the user; a script in the layout applies it before the
  page paints (`src/lib/palette.ts`).
