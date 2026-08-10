# MediaLister

**Status: In progress**

A personal media tracking application for managing movies, TV shows, and games across backlogs and yearly rankings. Replace Apple Notes–style lists with a single app that supports tags, streaming grouping, and ranked “Top of Year” lists with real-time sync.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Goal](#goal)
3. [Usage](#usage)
4. [File Structure](#file-structure)
5. [Running, Creating, and Hosting](#running-creating-and-hosting)
6. [Stack & Dependencies](#stack--dependencies)
7. [License](#license)

---

## Project Overview

MediaLister is a web app that replaces a Notes-based workflow for tracking:

- **Backlogs** — “To play/watch” lists for movies, TV shows, and games, organized by streaming service or platform (e.g., Max, Disney+, PC, PS5, Nintendo).
- **Rankings** — Yearly “Top of Year” lists where completed items are ranked with ratings (e.g., Perfect, Fantastic, Great for movies/shows; 10–4 + Sports for games) and optional metadata (time to beat, date added).

The app provides:

- **Unified data** — One entry per title with tags and flags (in theaters, currently streaming, currently watching, up next, watch with mom, etc.) so you don’t duplicate items across notes.
- **Check-off flow** — Checking an item moves it into the ranking flow (choose rating, position) while keeping it visible at the bottom of the backlog until year-end purge; uncheck to undo.
- **Filtering** — Tag-based filters (streaming, genre, year, “watch with mom”, “&lt; 2 hours”, etc.) that requery while keeping the same dropdown/group structure.
- **Consistent UI** — Tab-based navigation (Movies, TV Shows, Games on the left; “Check Top of Year” / “Check Backlogs” on the right), two-column layouts, and collapsible year dropdowns on ranking pages.
- **PWA** — Installable via “Add to Home Screen” with offline caching of static assets.
- **Real-time** — Supabase Realtime so changes appear across devices.

---

## Goal

- Replace scattered Apple Notes folders (“To play/watch”, “Top of Year”, etc.) with a single, structured app.
- Avoid retyping titles in multiple places (e.g., “In Theaters” vs streaming) by using one media row and flags/tags.
- Support flexible tags and highlights (gold = up next, red = currently watching, teal = second) and sort/filter without losing grouping.
- Seamlessly move completed items from backlog to yearly rankings with a simple check → rate → position flow.
- Keep ranking lists editable (e.g., “Edit order” and jump-to-position) and viewable by year (2022–2025 presets; 2026 and beyond as used).
- Achieve a clean, minimal UI (mostly white, dark mode) with no finicky menus—tabs only for navigation.

---

## Usage

### Backlogs

- **Movies**: Left column = streaming-service dropdowns (Max, Disney+, etc.) plus “No Streaming/Other”; right column = “In Theaters.” Use “Remove from theaters” to clear the flag and move to “No Streaming/Other.” Check off when watched to open ranking flow.
- **TV Shows**: Same dropdowns on the left; right = “Currently Streaming” with “Episodes released” to clear that flag. A “Currently Watching” strip spans the top. Red/gold/teal tags reorder within dropdowns.
- **Games**: Left = platform dropdowns (PC, PS5, Nintendo, Upcoming); right = “Currently Playing.” Tags include time-to-beat (e.g., “15 hrs”) and ownership (Owned, Waiting for sale, Game Pass). Check off when finished to rank.

### Rankings (Top of Year)

- Open via the right tab “Check Top of Year.” Sub-tabs: Top Movies / Top Shows / Top Games.
- Each view is an ordered list; years are in dropdowns with the current year (e.g., 2025) open by default.
- **Movies/Shows**: Rank #, title, rating (color-coded), release year, small date-added.
- **Games**: Rank #, title, rating, time to beat, expected time, small date-added.
- Ranks can be edited anytime (e.g., “Edit order,” jump to position). Insert at bottom by default; fractional positions (e.g., 13.5) for reordering, with optional normalization.

### Tags and Filters

- Highlights: red (currently watching), gold (up next), teal (second). Used to reorder within dropdowns and to populate “Currently Watching” / “Currently Playing” strips.
- Other tags: streaming service, in theaters, currently streaming, &lt; 2 hours (movies), watch with mom, watch with partner, genre, year, already watched, custom. Filters requery and show matching items in the same dropdown structure.

---

## File Structure

```
MediaLister/
├── public/
│   ├── favicon.ico
│   ├── icon-192.png
│   ├── icon-512.png
│   ├── apple-touch-icon.png
│   ├── manifest.webmanifest    # PWA manifest
│   ├── sw.js                   # Service worker
│   └── robots.txt
├── prisma/
│   └── schema.prisma           # Optional ORM schema
├── src/
│   ├── app/
│   │   ├── layout.tsx          # Root layout
│   │   ├── globals.css
│   │   ├── (auth)/
│   │   │   └── callback/
│   │   │       └── route.ts    # Auth callback (e.g. Supabase)
│   │   ├── api/
│   │   │   ├── media/route.ts       # CRUD for media/backlog items
│   │   │   ├── rankings/route.ts    # Create/update ranks
│   │   │   └── reorder/route.ts     # Reorder ranks
│   │   ├── backlog/
│   │   │   ├── layout.tsx      # Shared backlog shell + tab nav
│   │   │   ├── page.tsx        # Backlog landing/redirect
│   │   │   ├── movies/page.tsx
│   │   │   ├── shows/page.tsx
│   │   │   └── games/page.tsx
│   │   └── rankings/
│   │       ├── layout.tsx      # Rankings shell + year dropdowns
│   │       ├── page.tsx        # Rankings landing/redirect
│   │       ├── movies/page.tsx
│   │       ├── shows/page.tsx
│   │       └── games/page.tsx
│   ├── components/
│   │   ├── ui/                 # shadcn/ui components
│   │   ├── layout/
│   │   │   ├── tab-nav.tsx     # Backlogs vs Rankings + Movies/Shows/Games
│   │   │   ├── two-column.tsx  # Left/right column layout
│   │   │   └── year-accordion.tsx  # Year dropdowns on ranking pages
│   │   ├── backlog/
│   │   │   ├── streaming-section.tsx
│   │   │   ├── theaters-panel.tsx
│   │   │   ├── currently-watching-strip.tsx
│   │   │   ├── game-platform-section.tsx
│   │   │   ├── filters-bar.tsx
│   │   │   └── check-item-button.tsx
│   │   ├── rankings/
│   │   │   ├── rank-list.tsx
│   │   │   ├── rank-row.tsx
│   │   │   └── rank-edit-controls.tsx
│   │   └── modals/
│   │       └── rank-modal.tsx  # Check → rate → position flow
│   ├── lib/
│   │   ├── supabase-client.ts
│   │   ├── supabase-server.ts
│   │   ├── prisma.ts           # Optional
│   │   ├── constants.ts        # Ratings, colors, tag keys
│   │   ├── types.ts
│   │   ├── ranking.ts          # Insert/reorder helpers
│   │   ├── filters.ts          # Tag filter logic
│   │   └── pwa.ts              # PWA helpers if needed
│   ├── hooks/
│   │   ├── use-realtime.ts
│   │   ├── use-filter-state.ts
│   │   └── use-highlight-order.ts
│   ├── data/
│   │   └── seed/
│   │       ├── seed-media.ts
│   │       └── seed-rankings.ts  # 2022–2025 presets
│   └── styles/
│       ├── globals.css
│       └── shadcn.css
├── .env.local                  # Supabase URL/key (create manually)
├── .gitignore
├── next.config.js
├── package.json
├── postcss.config.js
├── tailwind.config.ts
└── tsconfig.json
```

---

## Running, Creating, and Hosting

### Local development

1. **Clone and install**
   - `git clone <repo-url> && cd MediaLister`
   - `npm install`

2. **Environment**
   - Create `.env.local` with Supabase credentials (e.g. `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`). Add a `.env.example` (without secrets) for reference.

3. **Database**
   - Set up Supabase project and run migrations (or Prisma migrations if using Prisma).
   - Optionally run seed scripts for 2022–2025 ranking presets: `src/data/seed/seed-media.ts`, `seed-rankings.ts`.

4. **Run**
   - `npm run dev` — app at `http://localhost:3000`.

### Creating the project (from scratch)

- Next.js app (App Router, TypeScript) created with `create-next-app` or equivalent.
- Tailwind + shadcn/ui added for styling.
- Supabase project created at [supabase.com](https://supabase.com); Postgres + Realtime enabled; optional Row Level Security (RLS) and auth if needed.
- Prisma (optional): `prisma init`, define schema, `prisma migrate` / `prisma generate`.
- PWA: `public/manifest.webmanifest` and `public/sw.js`; register service worker and add meta/link tags in layout.

### Hosting

- **Vercel (planned)** — Connect the GitHub repo to Vercel; use the free tier. Set the same env vars in the Vercel project. Deployments on push to `main` (or chosen branch).
- **Build** — `npm run build` (Next.js production build). Ensure no build errors before relying on auto-deploy.
- **PWA** — Serve over HTTPS (Vercel does this). Ensure manifest and service worker paths are correct for the production domain.

---

## Stack & Dependencies

| Layer        | Choice                          |
|-------------|----------------------------------|
| Frontend    | Next.js (App Router), TypeScript |
| Styling     | Tailwind CSS, shadcn/ui          |
| Database    | Supabase (Postgres)              |
| Realtime    | Supabase Realtime                |
| Auth        | Supabase Auth (if needed)        |
| ORM         | Prisma (optional)                |
| Hosting     | Vercel (free tier)               |
| PWA         | manifest.webmanifest + sw.js     |

---

## License

MIT
