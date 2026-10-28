#!/usr/bin/env python3
"""Download NCERT textbooks chapter by chapter, merge each book into one PDF and record chapter boundaries.

Standalone (Python 3.11+, PyMuPDF). Reads the catalogue `ncert_books.json` (metadata only) and fetches the
public files from https://ncert.nic.in/textbook/pdf/ at run time: `<code>cc.jpg` (cover), `<code>ps.pdf`
(prelims) and `<code>01.pdf .. <code>NN.pdf` (chapters). Polite: one request at a time, a pause between
requests, retries with back-off, a descriptive User-Agent, and files already present are skipped.

    python ncert_fetch.py --classes 6-8 --subjects sci,math --out work
    python ncert_fetch.py --books bk-ncert10-sci-en --out work

Per book it writes `<out>/<book_id>/<book_id>.pdf` (cover + prelims + chapters) and
`<out>/<book_id>/chapters.json` (1-based PDF page spans per chapter file and a heading guess).
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = "OnlineShalaJobs/1.0 (offline school textbook app; sequential polite fetch)"
HERE = Path(__file__).resolve().parent


def parse_range(spec: str) -> set[int]:
    out: set[int] = set()
    for part in filter(None, (p.strip() for p in spec.split(","))):
        a, _, b = part.partition("-")
        out.update(range(int(a), int(b or a) + 1))
    return out


def select(catalogue: dict, classes: str = "", subjects: str = "", books: str = "") -> list[dict]:
    cls = parse_range(classes) if classes else None
    subs = {s.strip() for s in subjects.split(",") if s.strip()}
    ids = {b.strip() for b in books.split(",") if b.strip()}
    out = []
    for b in catalogue["books"]:
        root = b["subject"].split("-")[0]
        if ids and b["id"] not in ids:
            continue
        if cls and b["class"] not in cls:
            continue
        if subs and not ({b["subject"], root, re.sub(r"\d+$", "", root)} & subs):
            continue
        out.append(b)
    return out


def chapter_files(book: dict) -> list[int]:
    """File numbers NN of `<code><NN>.pdf` (rationalised books skip some numbers: `gaps`)."""
    return [i for i in range(1, book["chapters"] + 1) if i not in set(book.get("gaps", []))]


def _valid(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 512:
        return False
    head = path.read_bytes()[:4]
    return head == b"%PDF" if path.suffix == ".pdf" else head[:3] == b"\xff\xd8\xff"


class Fetcher:
    def __init__(self, delay_s: float = 1.0, retries: int = 5, timeout_s: float = 90.0):
        self.delay_s, self.retries, self.timeout_s, self._last = delay_s, retries, timeout_s, 0.0
        try:  # verify against the OS trust store when available (some ncert.nic.in nodes need its chain)
            import truststore

            self.ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except ImportError:
            self.ssl_context = ssl.create_default_context()

    def get(self, url: str, dest: Path) -> bool:
        """True when downloaded, False on 404; raises after the retries."""
        err = ""
        for attempt in range(1, self.retries + 1):
            wait = self.delay_s - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=self.timeout_s, context=self.ssl_context) as r, open(dest.with_suffix(dest.suffix + ".part"), "wb") as f:
                    while chunk := r.read(1 << 16):
                        f.write(chunk)
                dest.with_suffix(dest.suffix + ".part").replace(dest)
                return True
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return False
                err = f"HTTP {e.code}"
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                err = f"{type(e).__name__}: {e}"
            if attempt < self.retries:
                time.sleep(min(60.0, 2.0 ** attempt))
        raise RuntimeError(f"{url}: {err}")


def download(book: dict, folder: Path, fetcher: Fetcher, base_url: str) -> list[str]:
    """Returns the names of required files that could not be fetched."""
    folder.mkdir(parents=True, exist_ok=True)
    code = book["code"]
    names = [(f"{code}cc.jpg", False), (f"{code}ps.pdf", False)]
    names += [(f"{code}{i:02d}.pdf", True) for i in chapter_files(book)]
    problems = []
    for name, required in names:
        dest = folder / name
        if _valid(dest):
            continue
        try:
            ok = fetcher.get(base_url + name, dest) and _valid(dest)
        except RuntimeError as e:
            print(f"  {name}: {e}", file=sys.stderr)
            ok = False
        if not ok:
            dest.unlink(missing_ok=True)
            if required:
                problems.append(name)
    return problems


def heading(pdf_path: Path) -> str | None:
    """Biggest text on the chapter's first page (a guess; the pipeline matches it against the contents page)."""
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        if not len(doc):
            return None
        lines = []
        for block in doc[0].get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                text = " ".join(s["text"] for s in line.get("spans", []) if s["text"].strip()).strip()
                if len(text) >= 3 and not re.fullmatch(r"[\W\d_]+|(?i:chapter|unit|reprint.*)", text):
                    lines.append((max(s["size"] for s in line["spans"]), line["bbox"][1], text))
    if not lines:
        return None
    top = max(s for s, _, _ in lines)
    return " ".join(t for s, _, t in sorted(lines, key=lambda l: l[1]) if s >= top - 1.5)[:90]


def merge(book: dict, folder: Path, out_dir: Path) -> dict:
    """Cover + prelims + chapters -> one PDF (one source file open at a time); returns the boundaries record."""
    import pymupdf

    code = book["code"]
    merged = pymupdf.open()
    cover = folder / f"{code}cc.jpg"
    if _valid(cover):
        with pymupdf.open(cover) as img, pymupdf.open("pdf", img.convert_to_pdf()) as pdf:
            merged.insert_pdf(pdf)
    prelims = folder / f"{code}ps.pdf"
    if _valid(prelims):
        with pymupdf.open(prelims) as src:
            merged.insert_pdf(src)
    chapters = []
    files = chapter_files(book)
    for k, i in enumerate(files, 1):
        path = folder / f"{code}{i:02d}.pdf"
        start = len(merged) + 1
        with pymupdf.open(path) as src:
            merged.insert_pdf(src)
        appendix = k > len(files) - book.get("appendix_files", 0)  # appendices: in the PDF, not lessons
        chapters.append({"number": k, "file": path.name, "heading": heading(path), "page_start": start, "page_end": len(merged),
                         "appendix": appendix})
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_out = out_dir / f"{book['id']}.pdf"
    merged.save(pdf_out, garbage=3, deflate=True)
    record = {"id": book["id"], "code": code, "class": book["class"], "subject": book["subject"], "title": book["title"],
              "language": book["language"], "pages": len(merged), "chapters": chapters}
    merged.close()
    (out_dir / "chapters.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--catalogue", default=str(HERE / "ncert_books.json"))
    ap.add_argument("--classes", default="")
    ap.add_argument("--subjects", default="", help="root slugs, e.g. sci,math,hin,san")
    ap.add_argument("--books", default="", help="comma-separated book ids")
    ap.add_argument("--out", default="work")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--list", action="store_true", help="print the selected book ids as a JSON list and exit")
    args = ap.parse_args(argv)
    catalogue = json.loads(Path(args.catalogue).read_text(encoding="utf-8"))
    books = select(catalogue, args.classes, args.subjects, args.books)
    if args.list:
        print(json.dumps([b["id"] for b in books]))
        return 0
    fetcher, failed = Fetcher(delay_s=args.delay), []
    for n, book in enumerate(books, 1):
        print(f"[{n}/{len(books)}] {book['id']} ({book['code']}, {book['chapters']} chapters)", flush=True)
        out_dir = Path(args.out) / book["id"]
        problems = download(book, out_dir / "src", fetcher, catalogue["base_url"])
        if problems:
            print(f"  missing: {problems}", file=sys.stderr)
            failed.append(book["id"])
            continue
        rec = merge(book, out_dir / "src", out_dir)
        print(f"  merged: {rec['pages']} pages", flush=True)
    if failed:
        print(f"incomplete: {failed}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
