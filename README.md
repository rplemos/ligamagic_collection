# LigaMagic Collection Comparer

Enter two LigaMagic collection IDs. The app lists every listing in the second
collection whose card name also appears in the first, and offers the result as
a TSV download.

Matching is on card name only, case-insensitive — a card counts as a match
regardless of edition, language, condition, or foiling.

The counts under the results show each collection's total number of cards, with
the number of distinct listings in parentheses.

Same logic is available on the command line:

```bash
python3 collection.py 123456 654321
```

See `WEBAPP.md` for running and deploying the web version.
