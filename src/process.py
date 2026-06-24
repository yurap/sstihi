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
import shutil
import subprocess
import sys
import tempfile
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


def convert_png_to_webp(png_path: Path, webp_path: Path, quality: int) -> None:
    cwebp = shutil.which("cwebp")
    if cwebp is None:
        raise RuntimeError("cwebp is required to write WebP images")
    webp_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [cwebp, "-quiet", "-q", str(quality), str(png_path), "-o", str(webp_path)],
        check=True,
    )


def render_page_image(
    doc,
    page_number: int,
    out_path: Path,
    scale: float,
    image_format: str,
    webp_quality: int,
) -> Path:
    if image_format == "png" or (image_format == "auto" and shutil.which("cwebp") is None):
        render_page_png(doc, page_number, out_path.with_suffix(".png"), scale=scale)
        return out_path.with_suffix(".png")

    webp_path = out_path.with_suffix(".webp")
    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        tmp_path = Path(tmp.name)
        render_page_png(doc, page_number, tmp_path, scale=scale)
        convert_png_to_webp(tmp_path, webp_path, webp_quality)
    return webp_path


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract pages/elements to JSON")
    parser.add_argument("book", type=str, help="PDF number(s) to process (comma-separated)")
    parser.add_argument("pages", nargs="?", help="Comma-separated list of 1-based pages")
    parser.add_argument("--stdout", action="store_true", help="Print JSON to stdout")
    parser.add_argument("--no-images", action="store_true", help="Skip page image rendering")
    parser.add_argument("--scale", type=float, default=2.0, help="Page render scale for page images")
    parser.add_argument("--title-scale", type=float, default=4.0, help="Scale for title image (page 1)")
    parser.add_argument("--title-small-scale", type=float, default=1.5, help="Scale for small title image")
    parser.add_argument(
        "--image-format",
        choices=["auto", "png", "webp"],
        default="auto",
        help="Image output format. auto writes WebP when cwebp is available, otherwise PNG.",
    )
    parser.add_argument("--webp-quality", type=int, default=80, help="WebP quality, 0-100")
    args = parser.parse_args()
    if not 0 <= args.webp_quality <= 100:
        print("--webp-quality must be between 0 and 100", file=sys.stderr)
        return 2

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

            pages_out = []
            elements = []
            # Render title images from page 1 (separate from page images).
            if not args.no_images:
                title_dir = base_dir / "data" / "images" / f"{book}"
                render_page_image(
                    doc,
                    1,
                    title_dir / "title",
                    scale=args.title_scale,
                    image_format=args.image_format,
                    webp_quality=args.webp_quality,
                )
                render_page_image(
                    doc,
                    1,
                    title_dir / "title_small",
                    scale=args.title_small_scale,
                    image_format=args.image_format,
                    webp_quality=args.webp_quality,
                )

            for p, (text, _title_hint, author) in zip(page_list, page_infos):
                print(f"book {book}: page {p}", file=sys.stderr)
                page_type = detect_type(text)
                image_path = None
                if args.no_images:
                    image_path = None
                else:
                    image_base_path = base_dir / "data" / "images" / f"{book}" / f"page_{p}"
                    rendered_path = render_page_image(
                        doc,
                        p,
                        image_base_path,
                        scale=args.scale,
                        image_format=args.image_format,
                        webp_quality=args.webp_quality,
                    )
                    image_path = rendered_path.relative_to(base_dir).as_posix()
                pages_out.append(
                    {
                        "page": p,
                        "text": text if page_type != "image" else None,
                        "image": image_path,
                    }
                )

                # Each page is its own display element.
                elements.append(
                    {
                        "start": p,
                        "end": p,
                        "author": author if page_type == "text" else None,
                        "type": page_type,
                    }
                )
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
