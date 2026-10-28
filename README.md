# online-shala-jobs

Build jobs for **Online Shala**, an offline textbook-study app for school students in Maharashtra.
This repository holds only **code and book metadata**. It is meant to be public so its GitHub Actions jobs can
run on standard runners.

## What it contains (and what it never contains)

| Contains | Never contains |
|---|---|
| `ncert_books.json`: the list of NCERT textbooks used (class, subject, title, NCERT file code, chapter count) | textbook PDFs, page images or extracted textbook text |
| `ncert_fetch.py`: downloads a book's public chapter PDFs from `ncert.nic.in` at run time and merges them | generated study content (summaries, questions, answers) |
| `extract_chunks.py`: extracts page text and paragraph chunks from a merged book | API keys, tokens or any other secret |
| `.github/workflows/ncert-books.yml`: a manual matrix workflow, one job per book | build outputs (`work/` is git-ignored) |

Textbooks are downloaded from `https://ncert.nic.in/textbook/pdf/` while a job runs and are discarded with the
runner. Results are delivered **only to a private destination**:

- **`private-repo`** (default): each job pushes `chapters.json`, `pages.jsonl` and `chunks.jsonl` (and, if
  asked, the merged PDF) to the private repository named by the `RESULTS_REPO` secret, using the
  `RESULTS_TOKEN` secret. `scripts/push_private.sh` checks through the GitHub API that the repository is
  private and refuses to push otherwise.
- **`none`**: a dry run; nothing leaves the runner.
- **`artifact-public-visible`**: uploads the results as a workflow artifact kept for 1 day. **Artifacts of a
  public repository can be downloaded by anyone who can see the run**, so this option is off by default and
  should only be used for a quick check of a single book.

## Running

1. Create a **private** repository for results (for example `online-shala-ncert-data`).
2. Add two repository secrets here: `RESULTS_REPO` (`owner/name` of that private repository) and
   `RESULTS_TOKEN` (a fine-grained token with *Contents: read and write* on that repository only).
3. Actions -> **ncert-books** -> *Run workflow*, with a class filter (`6`, `1-5`, `9-10`), an optional subject
   filter (`sci,math,hin,san`) or explicit book ids.

Each book is one job (about 5-20 minutes, limit 90 minutes; far below GitHub's 6-hour job limit). At most three
books run at a time, and each job fetches one file at a time with a pause between requests, so the NCERT server
sees polite, sequential traffic.

Locally:

```bash
pip install -r requirements.txt
python ncert_fetch.py --classes 6 --subjects sci --out work
python extract_chunks.py --work work
```

## Scope of the catalogue

Current NCERT editions (CBSE uses NCERT books): English-medium books for Classes 1-12 (Classes 11-12: Physics,
Chemistry, Biology, Mathematics and the English core readers), Hindi subject books for every class and Sanskrit
subject books for Classes 6-12. The textbooks are NCERT's; this project uses them with permission, and the
jobs only automate fetching the files NCERT publishes.
