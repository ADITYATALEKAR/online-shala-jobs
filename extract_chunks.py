#!/usr/bin/env python3
"""Text extraction and chunking job for books merged by `ncert_fetch.py`.

Standalone (Python 3.11+, PyMuPDF). For `<work>/<book_id>/` it reads `<book_id>.pdf` and `chapters.json` and
writes, next to them:
- `pages.jsonl`: one line per PDF page: {"page", "text", "devanagari_share", "latin_share", "needs_ocr"};
- `chunks.jsonl`: paragraph chunks of about 200-400 words per chapter, stable ids `<book_id>-<NN>-<nnn>`.

`needs_ocr` marks pages whose text layer is unusable for the book's language (empty pages that are mostly a
picture, or a Hindi/Sanskrit book whose text layer is a legacy, non-Unicode font): those pages are left for a
vision OCR step that runs on the private side, never here.

    python extract_chunks.py --work work --books bk-ncert06-sci-en
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

TARGET_MIN, TARGET_MAX = 200, 400
_DEV = re.compile(r"[ऀ-ॿ]")
_LAT = re.compile(r"[A-Za-z]")


def page_texts(pdf_path: Path) -> list[str]:
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        return [unicodedata.normalize("NFC", page.get_text()) for page in doc]


def shares(text: str) -> tuple[float, float]:
    dev, lat = len(_DEV.findall(text)), len(_LAT.findall(text))
    total = dev + lat
    return (dev / total, lat / total) if total else (0.0, 0.0)


def needs_ocr(text: str, language: str) -> bool:
    if len(text.strip()) < 40:
        return True
    dev, _ = shares(text)
    return language in ("hi", "sa") and dev < 0.3


def paragraphs(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    # PDF text has one line per printed line: a paragraph ends at a blank line or a line ending a sentence.
    paras, buf = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            if buf:
                paras.append(" ".join(buf))
                buf = []
            continue
        buf.append(line)
        if line.endswith((".", "?", "!", "।", "॥")) and len(" ".join(buf).split()) >= 40:
            paras.append(" ".join(buf))
            buf = []
    if buf:
        paras.append(" ".join(buf))
    return [p for p in paras if p.strip()]


def chunk_chapter(book_id: str, number: int, pages: list[tuple[int, str]]) -> list[dict]:
    chunks, buf, buf_page = [], [], None

    def flush() -> None:
        nonlocal buf, buf_page
        if buf:
            n = len(chunks) + 1
            chunks.append({"id": f"{book_id}-{number:02d}-{n:03d}", "chapter": number, "page": buf_page, "ord": n,
                           "text": "\n\n".join(buf)})
        buf, buf_page = [], None

    for page_no, text in pages:
        for para in paragraphs(text):
            words = len(para.split())
            if buf and len(" ".join(buf).split()) + words > TARGET_MAX:
                flush()
            if buf_page is None:
                buf_page = page_no
            buf.append(para)
            if len(" ".join(buf).split()) >= TARGET_MIN:
                flush()
    flush()
    return chunks


def process(book_dir: Path) -> dict:
    rec = json.loads((book_dir / "chapters.json").read_text(encoding="utf-8"))
    texts = page_texts(book_dir / f"{rec['id']}.pdf")
    with (book_dir / "pages.jsonl").open("w", encoding="utf-8") as f:
        for i, text in enumerate(texts, 1):
            dev, lat = shares(text)
            f.write(json.dumps({"page": i, "text": text, "devanagari_share": round(dev, 3), "latin_share": round(lat, 3),
                                "needs_ocr": needs_ocr(text, rec["language"])}, ensure_ascii=False) + "\n")
    total = 0
    with (book_dir / "chunks.jsonl").open("w", encoding="utf-8") as f:
        for ch in (c for c in rec["chapters"] if not c.get("appendix")):
            pages = [(p, texts[p - 1]) for p in range(ch["page_start"], ch["page_end"] + 1)
                     if not needs_ocr(texts[p - 1], rec["language"])]
            for c in chunk_chapter(rec["id"], ch["number"], pages):
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
                total += 1
    ocr = sum(1 for t in texts if needs_ocr(t, rec["language"]))
    return {"id": rec["id"], "pages": len(texts), "chunks": total, "pages_needing_ocr": ocr}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--work", default="work")
    ap.add_argument("--books", default="", help="comma-separated book ids (default: every merged book in --work)")
    args = ap.parse_args(argv)
    work = Path(args.work)
    ids = [b.strip() for b in args.books.split(",") if b.strip()] or sorted(p.name for p in work.iterdir() if (p / "chapters.json").exists())
    for book_id in ids:
        print(json.dumps(process(work / book_id)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
