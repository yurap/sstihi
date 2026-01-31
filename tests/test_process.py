from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.append(str(SRC))

import process  # noqa: E402


def test_title_override_pdf1():
    pdf = ROOT / "downloads" / "1.pdf"
    if not pdf.exists():
        return
    overrides = process.load_overrides(SRC / "title_overrides.json")
    title = process.extract_title(pdf, overrides=overrides)
    assert title == "Дневник одного Бёрдвотчера"


def test_title_override_pdf2():
    pdf = ROOT / "downloads" / "2.pdf"
    if not pdf.exists():
        return
    overrides = process.load_overrides(SRC / "title_overrides.json")
    title = process.extract_title(pdf, overrides=overrides)
    assert title == "русская классика"
