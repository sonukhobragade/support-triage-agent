# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Use GitHub's private vulnerability reporting on this repository:
**Security → Report a vulnerability**. That opens a private thread with the
maintainer.

Include what you found, how to reproduce it, and what an attacker gets. Expect a
first reply within a week. This is a personal project maintained in spare time,
so please size your expectations accordingly.

## Supported versions

The latest commit on the default branch. There are no maintained release
branches.

## Scope

In scope: anything that lets an unintended party read support mail, credentials,
or the local store; anything that lets a crafted email cause the agent to act
outside its intended path.

Out of scope: findings that depend on an attacker already having your `.env`, and
prompt-injection results against a model provider rather than this code.

## Worth knowing before you deploy this

These are design properties, not vulnerabilities, but they decide how you should
run it:

- **The store holds real customer mail.** `data/*.db` is a SQLite file
  containing the text of support conversations. It is gitignored, unencrypted,
  and should live somewhere your data-protection rules already cover, not on a
  laptop.
- **Guardrails are keyword-based.** `guardrails.py` reduces the chance of a bad
  draft reaching a reviewer. It does not remove it. Treat every draft as
  unreviewed until a human reviews it.
- **There is no send path.** The agent stops at the Notion ticket. If you add
  auto-send, you are taking on a risk this design deliberately avoids.
- **Redaction is best-effort.** `build_reply_library` strips phone numbers and
  email addresses it recognises. Verify it against your own data before trusting
  the output; a redactor only knows the identifier formats it was written for.

## If you leak a credential

Rotating is the fix. Deleting the key from a file, or rewriting git history, does
not revoke anything: assume any key that was ever committed is compromised and
issue a new one.
