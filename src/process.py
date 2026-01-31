#!/usr/bin/env python3
"""Print JSON per page with a heuristic page type.

Usage:
  process.py 12               # all pages
  process.py 12 1             # page 1
  process.py 12 1,3,5          # pages 1,3,5
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import fitz  # PyMuPDF
except ImportError:
    print("PyMuPDF is required. Install with: pip3 install PyMuPDF", file=sys.stderr)
    raise


def parse_pages(pages_arg: Optional[str]) -> Optional[List[int]]:
    if pages_arg is None:
        return None
    pages = []
    for part in pages_arg.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            num = int(part)
        except ValueError as exc:
            raise ValueError(f"Invalid page number: {part}") from exc
        if num < 1:
            raise ValueError("Pages are 1-based and must be >= 1")
        pages.append(num)
    if not pages:
        return None
    # Preserve order but remove duplicates
    seen = set()
    deduped = []
    for p in pages:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return deduped


def parse_books(books_arg: str) -> List[int]:
    books = []
    for part in books_arg.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_str, hi_str = part.split("-", 1)
            try:
                lo = int(lo_str)
                hi = int(hi_str)
            except ValueError as exc:
                raise ValueError(f"Invalid book range: {part}") from exc
            if lo < 1 or hi < 1 or hi < lo:
                raise ValueError(f"Invalid book range: {part}")
            books.extend(range(lo, hi + 1))
            continue
        try:
            num = int(part)
        except ValueError as exc:
            raise ValueError(f"Invalid book number: {part}") from exc
        if num < 1:
            raise ValueError("Book numbers must be >= 1")
        books.append(num)
    if not books:
        raise ValueError("No book numbers provided")
    # Preserve order but remove duplicates
    seen = set()
    deduped = []
    for b in books:
        if b not in seen:
            seen.add(b)
            deduped.append(b)
    return deduped




def median(values: List[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def page_title_hint(page) -> Tuple[bool, str]:
    data = page.get_text("dict")
    sizes: List[float] = []
    first_line_text = ""
    first_line_size = 0.0

    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            line_spans = []
            line_sizes = []
            for span in line.get("spans", []):
                text = span.get("text", "")
                if text == "":
                    continue
                line_spans.append(text)
                size = float(span.get("size", 0.0))
                if size > 0:
                    sizes.append(size)
                    line_sizes.append(size)
            if line_spans and not first_line_text:
                first_line_text = "".join(line_spans).strip()
                if line_sizes:
                    first_line_size = sum(line_sizes) / len(line_sizes)
            if first_line_text:
                break
        if first_line_text:
            break

    if not sizes or not first_line_text:
        return False, first_line_text

    med = median(sizes)
    # Consider title-like if first line noticeably larger than body text.
    is_title = first_line_size >= med * 1.25 and len(first_line_text) <= 120
    return is_title, first_line_text


def extract_page_lines_with_pos(page) -> List[Tuple[str, float]]:
    data = page.get_text("dict")
    lines_out: List[Tuple[str, float]] = []
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            parts = []
            for span in line.get("spans", []):
                text = span.get("text", "")
                if text != "":
                    parts.append(text)
            line_text = "".join(parts).strip()
            if line_text:
                x0 = float(line.get("bbox", [0, 0, 0, 0])[0])
                lines_out.append((line_text, x0))
    return lines_out


def extract_author_from_text(text: str) -> Tuple[str, Optional[str]]:
    tail = [ln.strip() for ln in text.rstrip().splitlines() if ln.strip()]
    if not tail:
        return text, None
    name_re = re.compile(
        r"^[А-ЯЁA-Z][а-яёa-z]+(?:\s+[А-ЯЁA-Z][а-яёa-z]+){1,2}$"
    )
    for cand in reversed(tail[-8:]):
        if cand.startswith("(") and cand.endswith(")"):
            continue
        if cand.isdigit():
            continue
        if 3 <= len(cand) <= 40 and name_re.match(cand):
            # Remove the author line from text if it's the last occurrence.
            lines2 = text.rstrip().splitlines()
            for i in range(len(lines2) - 1, -1, -1):
                if lines2[i].strip() == cand:
                    lines2.pop(i)
                    break
            text = "\n".join(lines2).rstrip() + "\n"
            return text, cand
    return text, None


def extract_page_info(doc, page_number: int) -> Tuple[str, bool, Optional[str]]:
    if page_number < 1 or page_number > doc.page_count:
        raise ValueError(f"Page {page_number} out of range (1..{doc.page_count})")
    page = doc.load_page(page_number - 1)
    text = page.get_text("text") or ""
    lines = text.splitlines()
    # Remove trailing page number if it's the last non-empty line.
    i = len(lines) - 1
    while i >= 0 and not lines[i].strip():
        i -= 1
    if i >= 0:
        last = lines[i].strip()
        if last.isdigit() and len(last) <= 4:
            lines = lines[:i]
            text = "\n".join(lines).rstrip() + "\n"

    author = None
    # Try to detect author line (often right-aligned) just before the page number.
    lines_pos = extract_page_lines_with_pos(page)
    if lines_pos:
        # Compute median x0 for body text.
        xs = [x for t, x in lines_pos if sum(ch.isalpha() for ch in t) >= 3]
        med_x = median(xs) if xs else 0.0
        last_text, last_x = lines_pos[-1]
        if last_text.isdigit() and len(last_text) <= 4 and len(lines_pos) >= 2:
            cand_text, cand_x = lines_pos[-2]
            letters = sum(ch.isalpha() for ch in cand_text)
            if 3 <= letters <= 40 and cand_x >= med_x + 20:
                author = cand_text
                # Remove author line from text if present at the end.
                tail = text.rstrip().splitlines()
                if tail and tail[-1].strip() == cand_text:
                    tail = tail[:-1]
                    text = "\n".join(tail).rstrip() + "\n"

    if author is None:
        text, author = extract_author_from_text(text)
    title_hint, _ = page_title_hint(page)
    return text, title_hint, author


def render_page_png(doc, page_number: int, out_path: Path, scale: float = 1.25) -> None:
    if page_number < 1 or page_number > doc.page_count:
        raise ValueError(f"Page {page_number} out of range (1..{doc.page_count})")
    page = doc.load_page(page_number - 1)
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_path))


def detect_type(text: str) -> str:
    if not text.strip():
        return "image"
    lower = text.lower()
    tech_keywords = [
        "isbn",
        "©",
        "copyleft",
        "copyright",
        "издательство",
        "тираж",
        "верстк",
        "редактор",
        "корректор",
        "дизайн",
        "иллюстрац",
        "печать",
        "типография",
        "г.",
    ]
    for kw in tech_keywords:
        if kw in lower:
            return "tech"
    intro_keywords = ["введение", "предисловие", "introduction"]
    for kw in intro_keywords:
        if lower.strip().startswith(kw) or f"\n{kw}" in lower:
            return "intro"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    total_chars = sum(len(ln) for ln in lines)
    letters = sum(ch.isalpha() for ch in text)
    if lines and len(lines) <= 3 and total_chars <= 120 and letters >= 3:
        first = lines[0]
        first_clean = re.sub(r"^[^0-9A-Za-zА-Яа-я]+", "", first)
        if first_clean and first_clean[0].islower():
            return "text"
        return "title"
    return "text"


def has_separator(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Allow separators like "***" or "* * *" (only asterisks and spaces).
        if all(ch == "*" for ch in stripped.replace(" ", "")) and stripped.count("*") >= 3:
            return True
    return False


def starts_like_new_poem(text: str, title_hint: bool) -> bool:
    if title_hint:
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    first = lines[0]
    first_clean = re.sub(r"^[^0-9A-Za-zА-Яа-я]+", "", first)
    # Ignore horizontal rule-like lines as titles.
    if re.fullmatch(r"[—-]+", first.strip()):
        return False
    # Date-like titles (e.g., "31 марта 2025", "31.03.2025") should start new items.
    date_words = (
        r"января|февраля|марта|апреля|мая|июня|июля|августа|"
        r"сентября|октября|ноября|декабря"
    )
    date_re = re.compile(rf"\b[0-3]?\d\s+({date_words})\s+20\d{{2}}\b", re.IGNORECASE)
    date_num_re = re.compile(r"\b[0-3]?\d[./-][01]?\d[./-]20\d{2}\b")
    if date_re.search(first_clean) or date_num_re.search(first_clean):
        return True
    letters = sum(ch.isalpha() for ch in first_clean)
    if letters < 3:
        return False
    # Short-ish first line looks like a title/new start, unless it starts lowercase.
    if len(first_clean) <= 120 and len(lines) <= 3:
        first_char = first_clean[0]
        if first_char.islower():
            return False
        return True
    # Short title line followed by body text (even if many lines).
    if len(first_clean) <= 40 and len(lines) >= 2:
        first_char = first_clean[0]
        if not first_char.islower():
            second = lines[1]
            letters2 = sum(ch.isalpha() for ch in second)
            if letters2 >= 10:
                return True
    # Title line followed by shouty line (e.g., title then ALL CAPS body).
    if len(first_clean) <= 40 and len(lines) >= 2:
        second = lines[1]
        letters2 = sum(ch.isalpha() for ch in second)
        if letters2 >= 5:
            upper2 = sum(ch.isupper() for ch in second)
            if upper2 >= int(letters2 * 0.7):
                return True
    # If first line is mostly uppercase, treat as new.
    upper = sum(ch.isupper() for ch in first_clean)
    if upper >= max(5, int(letters * 0.6)):
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract pages/elements to JSON")
    parser.add_argument("book", type=str, help="PDF number(s) to process (comma-separated)")
    parser.add_argument("pages", nargs="?", help="Comma-separated list of 1-based pages")
    parser.add_argument("--stdout", action="store_true", help="Print JSON to stdout")
    parser.add_argument("--no-images", action="store_true", help="Skip page image rendering")
    parser.add_argument("--scale", type=float, default=2.0, help="Page render scale for page images")
    parser.add_argument("--title-scale", type=float, default=4.0, help="Scale for title image (page 1)")
    parser.add_argument("--title-small-scale", type=float, default=1.5, help="Scale for small title image")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent.parent
    downloads_dir = base_dir / "downloads"
    try:
        books = parse_books(args.book)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        pages = parse_pages(args.pages)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for book in books:
        pdf_path = downloads_dir / f"{book}.pdf"
        if not pdf_path.exists():
            print(f"{pdf_path.name}\t(missing)", file=sys.stderr)
            continue

        try:
            doc = fitz.open(str(pdf_path))
            if pages is None:
                page_list = list(range(1, doc.page_count + 1))
            else:
                page_list = pages
            page_infos = [extract_page_info(doc, p) for p in page_list]

            item = 1
            prev_text = ""
            prev_item = None
            pages_out = []
            elements = []
            current_element = None
            # Render title images from page 1 (separate from page images).
            if not args.no_images:
                title_dir = base_dir / "data" / "images" / f"{book}"
                title_path = title_dir / "title.png"
                title_small_path = title_dir / "title_small.png"
                render_page_png(doc, 1, title_path, scale=args.title_scale)
                render_page_png(doc, 1, title_small_path, scale=args.title_small_scale)

            for p, (text, title_hint, author) in zip(page_list, page_infos):
                print(f"book {book}: page {p}", file=sys.stderr)
                page_type = detect_type(text)
                continuation_of = None
                if page_type == "text" and prev_text:
                    prev_lines = [ln for ln in prev_text.splitlines() if ln.strip()]
                    curr_lines = [ln for ln in text.splitlines() if ln.strip()]
                    if not has_separator(text) and not starts_like_new_poem(text, title_hint):
                        continuation_of = prev_item
                    elif (
                        not has_separator(text)
                        and len(prev_lines) > 6
                        and len(curr_lines) <= 6
                    ):
                        # Likely a page break in the middle of a poem.
                        continuation_of = prev_item
                image_path = None
                if args.no_images:
                    image_path = None
                else:
                    image_path = f"data/images/{book}/page_{p}.png"
                    render_page_png(doc, p, base_dir / image_path, scale=args.scale)
                pages_out.append(
                    {
                        "page": p,
                        "text": text if page_type != "image" else None,
                        "image": image_path,
                    }
                )

                # Build element ranges for manual tweaking later.
                if page_type != "text":
                    if current_element:
                        elements.append(current_element)
                        current_element = None
                    elements.append(
                        {
                            "start": p,
                            "end": p,
                            "author": None,
                            "type": page_type,
                        }
                    )
                else:
                    if continuation_of is None or current_element is None:
                        if current_element:
                            elements.append(current_element)
                        current_element = {
                            "start": p,
                            "end": p,
                            "author": author,
                            "type": "text",
                        }
                    else:
                        current_element["end"] = p
                        if author and not current_element.get("author"):
                            current_element["author"] = author
                prev_text = text
                prev_item = item
                item += 1
            if current_element:
                elements.append(current_element)
            output = {"pages": pages_out, "elements": elements}
            if args.stdout:
                print(json.dumps(output, ensure_ascii=False))
            else:
                out_dir = base_dir / "data"
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / f"{book}.json"
                out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
            doc.close()
        except Exception as exc:
            print(f"{pdf_path.name}\t(error: {exc})", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
