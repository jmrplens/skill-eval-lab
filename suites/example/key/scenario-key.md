# Answer key for the example suite

For each scenario, "Must" points are numbered and graded 1 / 0.5 / 0; "Must not"
points are listed with "- " and are violations when the answer does what they
forbid.

## S1 Breaking refactor of the logging module

Must:
1. Uses a Conventional Commits type prefix suited to a behaviour change, for example `refactor` or `feat`.
2. Signals the breaking change, either with a `!` after the type or scope (such as `refactor!:`) or a `BREAKING CHANGE:` footer, or both.
3. Writes the subject line in the imperative mood and keeps it a concise summary (roughly 50 characters or fewer).
4. Adds a body, separated from the subject by a blank line, that explains what changed and why it breaks consumers.

Must not:
- Invents a commit hash, issue number, author or scope that the request did not provide.

## S2 Fix a typo in the README

Must:
1. Uses the `docs:` type, since the change touches documentation only.
2. Writes the subject line in the imperative mood and keeps it concise.
3. Treats a single-line message as sufficient and does not pad it with an unnecessary body or footer.

Must not:
- Uses a code-oriented type such as `feat`, `fix` or `refactor` for a documentation-only change.
