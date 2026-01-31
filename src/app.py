#!/usr/bin/env python3
import json
import re
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

app = FastAPI()
app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")

templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")

EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "]+"
)
TAG_RE = re.compile(r"<[^>]+>")
STAR_ONLY_RE = re.compile(r"^\s*\*+\s*$")

def wrap_emoji(text: str) -> str:
    if not text:
        return text
    return EMOJI_RE.sub(lambda m: f'<span class=\"emoji\">{m.group(0)}</span>', text)


def _clean_line(line: str) -> str:
    if not line:
        return ""
    return TAG_RE.sub("", line).strip()

def _has_alnum(text: str) -> bool:
    for ch in text:
        if ch.isalnum():
            return True
    return False


def _extract_snippet(text: str, max_lines: int = 2) -> Optional[str]:
    if not text:
        return None
    lines = []
    counted = 0
    saw_first_text = False
    for line in text.splitlines():
        cleaned = _clean_line(line)
        if not cleaned:
            continue
        if STAR_ONLY_RE.match(cleaned) or not _has_alnum(cleaned):
            continue
        if not saw_first_text:
            saw_first_text = True
            if cleaned.lower() == "оглавление":
                return None
        lines.append(line.strip())
        counted += 1
        if counted >= max_lines:
            break
    if counted == 0:
        return None
    if not lines:
        return None
    return "\n".join(lines)


def pick_hero_snippet(book_id: int) -> Optional[dict]:
    items = merge_by_ranges(load_pages(book_id), load_elements(book_id))
    candidates = [(idx, it) for idx, it in enumerate(items, start=1) if it.get("text")]
    if any(it.get("type") == "text" for _, it in candidates):
        candidates = [(idx, it) for idx, it in candidates if it.get("type") == "text"]
    snippets = []
    for idx, it in candidates:
        snippet = _extract_snippet(it.get("text") or "")
        if snippet:
            snippets.append({"text": snippet, "anchor": f"item-{idx}"})
    if not snippets:
        return None
    return random.choice(snippets)

def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_pages(book_id: int) -> List[dict]:
    path = DATA_DIR / f"{book_id}.json"
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []

    # Try JSON first.
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "pages" in data:
            return data.get("pages") or []
        if isinstance(data, list):
            # Normalize list of ints -> list of page dicts
            if data and isinstance(data[0], int):
                return [{"page": p, "text": None} for p in data]
            return data
        if isinstance(data, dict):
            return [data]
    except json.JSONDecodeError:
        pass

    # Try concatenated JSON objects (pretty-printed JSONL via jq).
    decoder = json.JSONDecoder()
    idx = 0
    items = []
    length = len(text)
    while idx < length:
        while idx < length and text[idx].isspace():
            idx += 1
        if idx >= length:
            break
        try:
            obj, next_idx = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        items.append(obj)
        idx = next_idx
    if items:
        return items

    # Fallback: JSONL
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def load_elements(book_id: int) -> List[dict]:
    path = DATA_DIR / f"{book_id}.json"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "elements" in data:
            return data.get("elements") or []
    except json.JSONDecodeError:
        return []
    return []


def merge_continuations(items: List[dict]) -> List[dict]:
    merged = []
    id_map = {}
    for item in items:
        cont = item.get("continuation_of")
        if cont and cont in id_map:
            target = id_map[cont]
            if item.get("text"):
                target["text"] = (target.get("text") or "") + "\n" + item["text"]
            if item.get("author") and not target.get("author"):
                target["author"] = item["author"]
            if item.get("image"):
                imgs = target.setdefault("images", [])
                imgs.append(item["image"])
            target["pages"].append(item.get("page"))
            # Alias this item's id to the merged target for chained continuations.
            if item.get("item") is not None:
                id_map[item["item"]] = target
            continue

        base = dict(item)
        base["pages"] = [item.get("page")]
        if base.get("image"):
            base["images"] = [base["image"]]
        id_map[item.get("item")] = base
        merged.append(base)
    return merged


def merge_by_ranges(pages: List[dict], elements: List[dict]) -> List[dict]:
    if not elements:
        return pages

    items_by_page: Dict[int, List[dict]] = {}
    for it in pages:
        if not isinstance(it, dict):
            continue
        page = it.get("page")
        if isinstance(page, int):
            items_by_page.setdefault(page, []).append(it)

    used_pages = set()
    merged = []
    for el in elements:
        start = el.get("start")
        end = el.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        pages = list(range(start, end + 1))
        used_pages.update(pages)
        text_parts = []
        images = []
        notes = []
        for p in pages:
            for it in items_by_page.get(p, []):
                if it.get("text"):
                    text_parts.append(it["text"])
                if it.get("image"):
                    images.append(it["image"])
                if it.get("note"):
                    notes.append(it["note"])
        merged.append(
            {
                "pages": pages,
                "text": wrap_emoji("\n".join(text_parts).strip()) if text_parts else None,
                "note": "\n".join(notes).strip() if notes else None,
                "author": el.get("author"),
                "type": el.get("type"),
                "images": images or None,
            }
        )

    # Add any items not covered by ranges.
    for it in pages:
        if not isinstance(it, dict):
            continue
        page = it.get("page")
        if page in used_pages:
            continue
        merged.append(
            {
                "pages": [page],
                "text": wrap_emoji(it.get("text")),
                "note": it.get("note"),
                "author": None,
                "type": "page",
                "images": [it["image"]] if it.get("image") else None,
            }
        )
    return merged


def load_index() -> Dict[str, List[dict]]:
    path = DATA_DIR / "index.json"
    if not path.exists():
        return {"books": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"books": []}


def list_books() -> List[dict]:
    data = load_index()
    return data.get("books", [])


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    books = list_books()
    for b in books:
        book_id = b.get("id")
        if isinstance(book_id, int):
            title_small = DATA_DIR / "images" / str(book_id) / "title_small.png"
            title_large = DATA_DIR / "images" / str(book_id) / "title.png"
            if title_small.exists():
                b["cover"] = f"/data/images/{book_id}/title_small.png"
            elif title_large.exists():
                b["cover"] = f"/data/images/{book_id}/title.png"
            if title_large.exists():
                b["cover_large"] = f"/data/images/{book_id}/title.png"
    hero_candidates = [b for b in books if b.get("cover")]
    hero_book = random.choice(hero_candidates) if hero_candidates else (books[0] if books else None)
    hero_snippet = pick_hero_snippet(hero_book["id"]) if hero_book and hero_book.get("id") else None
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "books": books,
            "hero_book": hero_book,
            "hero_snippet": hero_snippet,
        },
    )


@app.get("/book/{book_id}", response_class=HTMLResponse)
def book_view(book_id: int, request: Request):
    items = merge_by_ranges(load_pages(book_id), load_elements(book_id))
    book_meta = next((b for b in list_books() if b.get("id") == book_id), None)
    return templates.TemplateResponse(
        "book.html",
        {
            "request": request,
            "book_id": book_id,
            "book_meta": book_meta,
            "items": items,
        },
    )
