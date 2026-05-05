# Sstihi

Minimal pipeline for extracting PDF booklets into JSON and serving them via a FastAPI web app.

## Requirements

- Python 3.9+
- `pip` / virtualenv

Install runtime dependencies:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

The deployed web app only needs FastAPI, Uvicorn, and Jinja. PDF processing
dependencies such as PyMuPDF are intentionally kept in `requirements-dev.txt`
so they are not installed into the production image.

## Process PDFs

Install local-only deps:

```bash
python3 -m pip install -r requirements-dev.txt
```

`src/process.py` reads PDFs from `downloads/` and writes JSON to `data/{book}.json` by default.

Basic usage:

```bash
python3 src/process.py 4
```

Multiple books (comma-separated or ranges):

```bash
python3 src/process.py 1,3,5
python3 src/process.py 1-6
python3 src/process.py 1,3-6,9
```

Page selection (optional):

```bash
python3 src/process.py 4 1,2,3
```

Options:

- `--stdout` — print JSON to stdout instead of writing `data/{book}.json`
- `--scale` — scale for page images (default `2.0`)
- `--title-scale` — scale for title image (default `4.0`)
- `--title-small-scale` — scale for tile background (default `1.5`)
- `--image-format` — `auto`, `webp`, or `png` (default `auto`; writes WebP when `cwebp` is installed)
- `--webp-quality` — WebP quality (default `80`)
- `--no-images` — skip rendering images

### Output files

For each book:

- `data/{book}.json`
- `data/images/{book}/page_{n}.webp` (or `.png` without `cwebp`)
- `data/images/{book}/title.webp` (or `.png` without `cwebp`)
- `data/images/{book}/title_small.webp` (or `.png` without `cwebp`)

## Convert Existing Images To WebP

Install WebP tools so the `cwebp` command is available, then run:

```bash
python3 scripts/convert_images_to_webp.py --quality 80 --delete-originals
```

Use `--dry-run` first to preview the conversions and JSON path rewrites without
changing files.

## JSON formats

### `data/{book}.json`

```json
{
  "pages": [
    {"page": 1, "text": "...", "image": "data/images/1/page_1.webp", "note": "editorial note"},
    {"page": 2, "text": "...", "image": "data/images/1/page_2.webp"}
  ],
  "elements": [
    {"start": 1, "end": 2, "author": "...", "type": "text"},
    {"start": 3, "end": 3, "author": null, "type": "image"}
  ]
}
```

- `pages` is a list of page records with text and image path; optional `note` is shown as a distinct note in text mode.
- `elements` is a list of merged ranges (start/end inclusive) with a `type` and optional `author`.
- Each page should belong to some element (you can manually edit the ranges to fix grouping).

### `data/index.json`

```json
{
  "books": [
    {"id": 1, "title": "", "url": "https://..."}
  ]
}
```

`title` can be filled manually; `url` is the original Google Drive link.

## Run the web app

```bash
./.venv/bin/uvicorn src.app:app --reload
```

Open: http://127.0.0.1:8000

- Homepage uses `data/index.json` + `title_small.webp` for tiles and `title.webp` for the hero, with PNG fallback.
- Book page uses `data/{book}.json` and renders images + merged text ranges.
