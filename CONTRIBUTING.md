# Contributing

Thanks for taking a look. This is a small project, so the process is short.

## Getting set up

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

The tests do not need credentials. `.env` is only needed to run the pipeline
against a real inbox.

## Before you open a pull request

Run the gate:

```bash
bash tools/local_gate.sh
```

That is lint, unit tests, and a collection smoke check. CI runs the same script,
so a green gate locally means a green gate on GitHub. If the gate is red, fix
the code. Do not weaken a check to make it pass; a check that was loosened to go
green is worse than no check, because it still reads as coverage.

## What makes a good change here

- A bug fix comes with a test that fails before the fix and passes after it.
- Guardrail changes need a test for the thing the guard is supposed to stop.
  `tests/test_guardrails.py` shows the shape.
- Threshold and policy constants are deliberate judgement calls. Changing one
  changes every output, so explain why in the PR rather than only in the diff.

## What not to send

**Never include real support mail, customer names, email addresses, phone
numbers, ticket exports, or anything derived from a production mailbox** — not
in a test fixture, not in an issue, not in a screenshot. A support inbox pairs
customer contact details with the full text of their complaints and disputes. If
you need a fixture, write a synthetic one; `data/reply_library.json` shows the
schema.

The same applies to API keys and `.env` files. `.gitignore` blocks the obvious
paths, but it cannot catch a key pasted into an issue comment.

## Reporting bugs

Open an issue with what you ran, what happened, and what you expected. If it
involves an email, paraphrase it rather than pasting it.
