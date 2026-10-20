# Scenarios for the example suite

Each section is `## S<n> <title>` followed by the request the model answers,
once with the skill installed and once without it.

## S1 Breaking refactor of the logging module

I refactored the logging module so it emits structured JSON logs instead of
plain text. Anything that parses our log output will break until it is updated,
so this is a breaking change. Write the git commit message for it.

## S2 Fix a typo in the README

I fixed a single spelling mistake in the README ("recieve" to "receive"). Write
the git commit message for it.
