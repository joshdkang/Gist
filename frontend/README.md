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
- **Pages**: `/` (your entities and the public samples), `/entities/new`, `/entities/[id]` (themes,
  emerging topics, run status, and the owner's upload, edit and delete), `/entities/[id]/themes/[themeId]`
  (sentiment, trend by month, quarter or year, and the reviews), and `/entities/[id]/upload` (choose a CSV,
  map its columns, upload).
- **Upload** reads the CSV in the browser (Papa Parse) to count rows, guess the column mapping (the review
  text is the column with the longest values) and the rating scale, and preview the first rows. The API
  re-checks everything.
- **Colours** are tokens in `src/app/globals.css`, with a dark set. Sentiment: positive green, mixed
  yellow-orange, neutral light grey, negative red; every colour also has a text label. Colour-blind mode, a
  switch in the account menu under the email, swaps in positive blue, mixed bluish green, neutral grey
  and negative orange (`data-palette="colorblind"` on `<html>`). The choice is saved in the browser and
  in the Supabase user's metadata, so it follows the user; a script in the layout applies it before the
  page paints (`src/lib/palette.ts`).
