# Gallery templates

The Studio's **Gallery** page installs these into its agent with one click and
opens the one you picked on the Canvas. Each folder is one skill written as
`steps:`, with its tools beside it in `scripts.py`, so it installs as one folder.

| Template | What it uses |
|---|---|
| [`expense-report`](expense-report) | a `repeat:` that collects one line per expense, a total worked out by a lookup, a confirmation, and a manager's yes (`by: manager`) for a report of $500 or more |
| [`subscription-help`](subscription-help) | a pick from a designed grid (`from:` with `widget:`), a designed details card (`say:` with `widget:`), then a `chat:` whose lookup reads only the chosen subscription |
| [`table-booking`](table-booking) | a pick from a list, answers with a shape (`pattern:`), a rule a lookup decides (`check:`), and a card asked only for parties over 8 (`when:`) |

The data in each `scripts.py` stands in for your own system: replace the
function bodies and keep their signatures. To use a template outside the
Studio, copy its folder into your skills folder.

Each one runs as a conversation, with no API key, in
[`backend/tests/test_gallery_templates.py`](../../backend/tests/test_gallery_templates.py):

```bash
pytest backend/tests/test_gallery_templates.py
```
