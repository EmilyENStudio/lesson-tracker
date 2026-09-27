#!/usr/bin/env python3
"""Build and validate stable links between Dahl reader chapters and plan units."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path


BOOKS = {
    "magic-finger": ("books/magic-finger/the-magic-finger/book.json", "frontend/roald-dahl/teaching-v2/magic-finger/index.html"),
    "fox": ("books/roald-dahl/fox/book.json", "frontend/roald-dahl/teaching-v2/fox/index.html"),
    "crocodile": ("books/roald-dahl/crocodile/book.json", "frontend/roald-dahl/teaching-v2/crocodile/index.html"),
    "charlie": ("books/roald-dahl/charlie/book.json", "frontend/roald-dahl/teaching-v2/charlie/index.html"),
    "george": ("books/roald-dahl/george/book.json", "frontend/roald-dahl/teaching-v2/george/index.html"),
    "esio-trot": ("books/roald-dahl/esio-trot/book.json", "frontend/roald-dahl/teaching-v2/esio-trot/index.html"),
}
MANIFEST_PATH = Path("frontend/roald-dahl/chapter-plan-links.json")


class PlanIndex(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.units: list[dict[str, str]] = []
        self._unit: dict[str, str] | None = None
        self._capture_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key: value or "" for key, value in attrs}
        if tag == "article" and "unit" in attrs_map.get("class", "").split():
            self._unit = {"title": "", "planHref": ""}
        elif self._unit is not None and tag == "h2":
            self._capture_title = True
        elif self._unit is not None and tag == "a":
            href = attrs_map.get("href", "")
            if href.endswith("-plan.html"):
                self._unit["planHref"] = href

    def handle_data(self, data: str) -> None:
        if self._unit is not None and self._capture_title:
            self._unit["title"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2":
            self._capture_title = False
        if tag == "article" and self._unit is not None:
            self._unit["title"] = html.unescape(self._unit["title"]).strip()
            self.units.append(self._unit)
            self._unit = None


def load_book(root: Path, slug: str) -> tuple[dict, list[dict[str, str]]]:
    book_rel, plan_rel = BOOKS[slug]
    book_path = root / book_rel
    plan_path = root / plan_rel
    book = json.loads(book_path.read_text(encoding="utf-8"))
    parser = PlanIndex()
    parser.feed(plan_path.read_text(encoding="utf-8"))
    return book, parser.units


def build(root: Path) -> dict:
    links = []
    for slug, (book_rel, plan_rel) in BOOKS.items():
        book, units = load_book(root, slug)
        pages = book.get("pages", [])
        if len(pages) != len(units):
            raise ValueError(f"{slug}: {len(pages)} reader pages but {len(units)} plan units")
        book_link = {
            "bookId": book["id"],
            "slug": slug,
            "readerSource": book_rel,
            "teachingPlanIndex": plan_rel,
            "units": [],
        }
        for index, (page, unit) in enumerate(zip(pages, units), start=1):
            if page.get("title", "").strip() != unit["title"]:
                raise ValueError(f"{slug} unit {index}: reader and plan titles differ")
            expected_href = f"unit-{index}-plan.html"
            if unit["planHref"] != expected_href:
                raise ValueError(f"{slug} unit {index}: expected plan link {expected_href}, got {unit['planHref']!r}")
            plan_page = (root / plan_rel).parent / expected_href
            if not plan_page.is_file():
                raise ValueError(f"{slug} unit {index}: missing plan file {plan_page}")
            book_link["units"].append({
                "unitId": f"dahl-{slug}-unit-{index:02d}",
                "readerPageId": page["id"],
                "title": page["title"],
                "planPage": str(plan_page.relative_to(root)).replace("\\", "/"),
                "sourceChapterNumbers": page.get("sourceChapterNumbers", []),
            })
        links.append(book_link)
    return {"schemaVersion": 1, "relationship": "reader-page-to-teaching-plan-unit", "books": links}


def validate(root: Path, manifest: dict) -> list[str]:
    errors: list[str] = []
    slugs_seen: set[str] = set()
    unit_ids_seen: set[str] = set()
    for entry in manifest.get("books", []):
        slug = entry.get("slug", "")
        if slug not in BOOKS or slug in slugs_seen:
            errors.append(f"invalid or duplicate book slug: {slug}")
            continue
        slugs_seen.add(slug)
        try:
            book, plan_units = load_book(root, slug)
        except Exception as exc:
            errors.append(f"{slug}: unable to read sources: {exc}")
            continue
        if entry.get("bookId") != book.get("id"):
            errors.append(f"{slug}: book ID changed")
        if entry.get("readerSource") != BOOKS[slug][0] or entry.get("teachingPlanIndex") != BOOKS[slug][1]:
            errors.append(f"{slug}: source path changed")
        pages = {page.get("id"): page for page in book.get("pages", [])}
        page_ids_seen: set[str] = set()
        plan_by_href = {f"unit-{i}-plan.html": unit for i, unit in enumerate(plan_units, 1)}
        for link in entry.get("units", []):
            unit_id = link.get("unitId", "")
            if not unit_id or unit_id in unit_ids_seen:
                errors.append(f"{slug}: missing/duplicate unit ID {unit_id!r}")
            unit_ids_seen.add(unit_id)
            page_id = link.get("readerPageId", "")
            page = pages.get(page_id)
            if page is None or page_id in page_ids_seen:
                errors.append(f"{slug}: missing/duplicate reader page {page_id!r}")
                continue
            page_ids_seen.add(page_id)
            if link.get("title") != page.get("title"):
                errors.append(f"{slug}/{page_id}: stale reader title")
            plan_path = Path(link.get("planPage", ""))
            plan_rel = Path(BOOKS[slug][1]).parent / Path(f"unit-{len(page_ids_seen)}-plan.html")
            expected_rel = plan_rel.as_posix()
            if link.get("planPage") != expected_rel:
                errors.append(f"{slug}/{page_id}: expected plan source {expected_rel}")
            if not (root / plan_path).is_file():
                errors.append(f"{slug}/{page_id}: missing plan file {plan_path}")
            unit = plan_by_href.get(plan_path.name)
            if unit is None or unit["title"] != page.get("title"):
                errors.append(f"{slug}/{page_id}: plan heading does not match reader chapter")
            if link.get("sourceChapterNumbers", []) != page.get("sourceChapterNumbers", []):
                errors.append(f"{slug}/{page_id}: original chapter scope changed")
        if page_ids_seen != set(pages):
            errors.append(f"{slug}: mapping covers {len(page_ids_seen)} of {len(pages)} reader pages")
    if slugs_seen != set(BOOKS):
        errors.append(f"manifest must cover all Dahl books: missing {sorted(set(BOOKS) - slugs_seen)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, help="site root containing books/ and frontend/")
    parser.add_argument("--build", action="store_true", help="generate the association manifest")
    args = parser.parse_args()
    root = args.root.resolve()
    manifest_path = root / MANIFEST_PATH
    try:
        if args.build:
            manifest = build(root)
            errors = validate(root, manifest)
            if errors:
                raise ValueError("\n".join(errors))
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            errors = validate(root, manifest)
            if errors:
                print("\n".join(errors), file=sys.stderr)
                return 1
        print(f"OK: {sum(len(book['units']) for book in manifest['books'])} chapter-plan links across {len(manifest['books'])} Dahl books")
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
