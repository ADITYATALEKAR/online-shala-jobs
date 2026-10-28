#!/usr/bin/env bash
# Copies one book's results into the PRIVATE results repository and pushes them.
# Refuses to push unless the GitHub API confirms that the destination repository is private.
set -euo pipefail

book_dir="$1"
book_id="$(basename "$book_dir")"
: "${RESULTS_REPO:?set the RESULTS_REPO secret (owner/name of a private repository)}"
: "${RESULTS_TOKEN:?set the RESULTS_TOKEN secret}"

private=$(curl -fsS -H "Authorization: Bearer ${RESULTS_TOKEN}" -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/${RESULTS_REPO}" | python -c 'import json,sys; print(json.load(sys.stdin).get("private"))')
if [ "$private" != "True" ]; then
  echo "::error::${RESULTS_REPO} is not a private repository; refusing to push textbook text there"
  exit 1
fi

dest="$(mktemp -d)"
git clone --depth 1 "https://x-access-token:${RESULTS_TOKEN}@github.com/${RESULTS_REPO}.git" "$dest" >/dev/null 2>&1
mkdir -p "$dest/ncert/$book_id"
cp "$book_dir/chapters.json" "$book_dir/pages.jsonl" "$book_dir/chunks.jsonl" "$dest/ncert/$book_id/"
if [ "${INCLUDE_PDF:-false}" = "true" ]; then
  pdf="$book_dir/$book_id.pdf"
  if [ "$(stat -c %s "$pdf")" -lt 95000000 ]; then cp "$pdf" "$dest/ncert/$book_id/"; else echo "skipping $pdf (over 95 MB)"; fi
fi

cd "$dest"
git config user.name "online-shala-jobs"
git config user.email "online-shala-jobs@users.noreply.github.com"
git add "ncert/$book_id"
if git diff --cached --quiet; then echo "no changes for $book_id"; exit 0; fi
git commit -q -m "ncert: $book_id"
for attempt in 1 2 3 4 5; do  # matrix jobs push concurrently
  if git push -q origin HEAD; then echo "pushed $book_id"; exit 0; fi
  sleep $((attempt * 5))
  git pull -q --rebase
done
echo "::error::push failed for $book_id"
exit 1
