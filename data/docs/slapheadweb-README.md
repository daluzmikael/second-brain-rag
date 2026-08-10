# Slaphead Web (Static MVP)

This project is a simple static website for the early/basic version of the Slaphead music site.

It uses plain HTML, CSS, and JavaScript with no build tools or framework.
Visitors enter a code on the homepage, and valid codes route them to hidden song pages with embedded audio players.

## Project Overview

- `index.html` is the landing page with the unlock input + button.
- `script.js` handles code input normalization and page routing.
- `style.css` contains shared styling for the main and song pages.
- `songs/` contains one HTML page per track.
- `assets/` stores the `.mp3` files used by the song pages.

## Project Structure

```text
slapheadweb/
├── assets/
│   ├── drunktrunk.mp3
│   └── saveme.mp3
├── songs/
│   ├── drunktrunk.html
│   └── saveme.html
├── index.html
├── script.js
├── style.css
└── README.md
```

## How It Works

1. User opens `index.html`.
2. User enters a code (example: `drunktrunk` or `saveme`).
3. `handleCodeSubmit()` in `script.js`:
   - trims whitespace
   - lowercases input
   - removes internal spaces
4. If the code exists in `codeMap`, browser navigates to the matching file in `songs/`.
5. If not, an invalid-code alert is shown.

## Run Locally

Because this is a static site, you can either:

- open `index.html` directly in your browser, or
- serve the folder locally (recommended):

```bash
python3 -m http.server 8000
```

Then open `http://localhost:8000`.

## Adding a New Song

1. Add your audio file to `assets/` (for example `newtrack.mp3`).
2. Create `songs/newtrack.html` using the existing song pages as a template.
3. Add a new code entry in `script.js`:

```js
const codeMap = {
  "drunktrunk": "songs/drunktrunk.html",
  "saveme": "songs/saveme.html",
  "newtrack": "songs/newtrack.html"
};
```

4. Test by entering the new code on the homepage.

## Notes

- This repo is intentionally minimal and easy to host anywhere static files are supported (GitHub Pages, Netlify, Vercel static, etc.).
- If you want to evolve this into a fuller Slaphead site later, this can stay as the lightweight baseline.
