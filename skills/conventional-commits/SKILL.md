---
name: conventional-commits
description: Use when writing a git commit message, so the message follows the Conventional Commits format: a type prefix, an imperative summary, an optional body explaining why, and a marker for breaking changes.
version: 1.0.0
license: MIT
---

# Conventional Commits

Write the commit message as a Conventional Commit.

## Structure

```text
<type>(<optional scope>): <summary>

<optional body>

<optional footer>
```

- **Subject line**: `<type>(<scope>): <summary>`, in the imperative mood ("add",
  not "added" or "adds"), no trailing period, kept to roughly 50 characters.
- **Body**: optional, separated from the subject by one blank line. Use it to
  explain what changed and, above all, *why*. Wrap at about 72 columns.
- **Footer**: optional, for breaking-change notes and references (for example
  `Closes #123`).

## Types

- `feat` a new feature
- `fix` a bug fix
- `docs` documentation only
- `style` formatting that does not change behaviour
- `refactor` a change that neither fixes a bug nor adds a feature
- `perf` a performance improvement
- `test` adding or correcting tests
- `build` the build system or dependencies
- `ci` CI configuration
- `chore` anything else that does not touch `src` or tests

Pick the type from what the change actually does, not from the file it touches:
a documentation-only change is `docs` even when it sits next to code.

## Breaking changes

Signal a breaking change in one of two ways (or both):

- a `!` right after the type or scope, for example `feat!:` or `refactor(api)!:`;
- a `BREAKING CHANGE:` footer describing what breaks and what a consumer must do.

A breaking change is anything that forces someone downstream to change their
code, configuration, or the way they call you.

## Keep it honest

Describe only what the change does. Do not invent an issue number, a commit
hash, a scope, or a co-author that you were not given.
