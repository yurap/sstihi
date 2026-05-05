#!/usr/bin/env python3
"""Convert generated PNG page images to WebP and update data JSON paths."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
IMAGES_DIR = DATA_DIR / "images"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quality", type=int, default=80, help="WebP quality, 0-100")
    parser.add_argument("--force", action="store_true", help="Recreate existing WebP files")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="PNG_PATH",
        help="Convert only this PNG path. Can be passed multiple times.",
    )
    parser.add_argument(
        "--delete-originals",
        action="store_true",
        help="Delete PNG files after successful conversion and JSON rewrite",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes only")
    parser.add_argument("--verbose", action="store_true", help="Print every converted/deleted file")
    return parser.parse_args()


def resolve_png_paths(paths: list[str]) -> list[Path]:
    resolved = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_absolute():
            path = ROOT / path
        path = path.resolve()
        try:
            path.relative_to(IMAGES_DIR.resolve())
        except ValueError:
            raise ValueError(f"{raw_path} is outside data/images")
        if path.suffix.lower() != ".png":
            raise ValueError(f"{raw_path} is not a PNG path")
        if not path.exists():
            raise ValueError(f"{raw_path} does not exist")
        resolved.append(path)
    return sorted(set(resolved))


def convert_png(
    cwebp: str,
    png_path: Path,
    quality: int,
    force: bool,
    dry_run: bool,
    verbose: bool,
) -> bool:
    webp_path = png_path.with_suffix(".webp")
    if webp_path.exists() and not force:
        return False
    if dry_run:
        if verbose:
            print(f"convert {png_path.relative_to(ROOT)} -> {webp_path.relative_to(ROOT)}")
        return True
    subprocess.run(
        [cwebp, "-quiet", "-q", str(quality), str(png_path), "-o", str(webp_path)],
        check=True,
    )
    return True


def rewrite_image_paths(value: Any, available_webps: set[Path]) -> tuple[Any, int]:
    if isinstance(value, dict):
        changed = 0
        out = {}
        for key, item in value.items():
            rewritten, item_changed = rewrite_image_paths(item, available_webps)
            out[key] = rewritten
            changed += item_changed
        return out, changed
    if isinstance(value, list):
        changed = 0
        out = []
        for item in value:
            rewritten, item_changed = rewrite_image_paths(item, available_webps)
            out.append(rewritten)
            changed += item_changed
        return out, changed
    if isinstance(value, str) and value.endswith(".png"):
        normalized = value.lstrip("/")
        if normalized.startswith("data/images/"):
            webp_path = ROOT / normalized
            webp_path = webp_path.with_suffix(".webp")
            if webp_path in available_webps:
                return value[:-4] + ".webp", 1
    return value, 0


def rewrite_json_files(available_webps: set[Path], dry_run: bool) -> int:
    total_changed = 0
    for path in sorted(DATA_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"skip invalid JSON {path.relative_to(ROOT)}: {exc}", file=sys.stderr)
            continue
        rewritten, changed = rewrite_image_paths(data, available_webps)
        if changed == 0:
            continue
        total_changed += changed
        print(f"rewrite {path.relative_to(ROOT)}: {changed} path(s)")
        if not dry_run:
            path.write_text(
                json.dumps(rewritten, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    return total_changed


def delete_originals(dry_run: bool, verbose: bool) -> int:
    deleted = 0
    for png_path in sorted(IMAGES_DIR.glob("**/*.png")):
        if not png_path.with_suffix(".webp").exists():
            continue
        deleted += 1
        if dry_run:
            if verbose:
                print(f"delete {png_path.relative_to(ROOT)}")
        else:
            png_path.unlink()
    return deleted


def delete_selected_originals(png_paths: list[Path], dry_run: bool, verbose: bool) -> int:
    deleted = 0
    for png_path in png_paths:
        if not png_path.with_suffix(".webp").exists():
            continue
        deleted += 1
        if dry_run:
            if verbose:
                print(f"delete {png_path.relative_to(ROOT)}")
        else:
            png_path.unlink()
    return deleted


def main() -> int:
    args = parse_args()
    if not 0 <= args.quality <= 100:
        print("--quality must be between 0 and 100", file=sys.stderr)
        return 2
    cwebp = shutil.which("cwebp")
    if cwebp is None:
        print("cwebp is required. Install WebP tools first.", file=sys.stderr)
        return 2
    if not IMAGES_DIR.exists():
        print(f"{IMAGES_DIR.relative_to(ROOT)} does not exist", file=sys.stderr)
        return 2

    try:
        png_paths = resolve_png_paths(args.only) if args.only else sorted(IMAGES_DIR.glob("**/*.png"))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    available_webps = set(IMAGES_DIR.glob("**/*.webp"))
    converted = 0
    for png_path in png_paths:
        try:
            if convert_png(cwebp, png_path, args.quality, args.force, args.dry_run, args.verbose):
                converted += 1
            if png_path.with_suffix(".webp").exists() or args.dry_run:
                available_webps.add(png_path.with_suffix(".webp"))
        except subprocess.CalledProcessError as exc:
            print(f"conversion failed for {png_path.relative_to(ROOT)}: {exc}", file=sys.stderr)
            return 1

    rewritten = rewrite_json_files(available_webps, args.dry_run)
    if args.delete_originals and args.only:
        deleted = delete_selected_originals(png_paths, args.dry_run, args.verbose)
    elif args.delete_originals:
        deleted = delete_originals(args.dry_run, args.verbose)
    else:
        deleted = 0
    print(
        f"converted={converted} json_paths_rewritten={rewritten} "
        f"deleted_pngs={deleted} total_pngs={len(png_paths)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
