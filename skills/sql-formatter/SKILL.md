---
name: sql-formatter
description: Use when asked to format, prettify, or reindent a SQL query, to lay it out with uppercase keywords, one clause per line, and consistent indentation.
version: 1.0.0
license: MIT
---

# SQL Formatter

Reformat a SQL query for readability without changing what it does.

- Uppercase SQL keywords (`SELECT`, `FROM`, `WHERE`, `JOIN`, `ORDER BY`).
- Put each top-level clause on its own line.
- Put each selected column on its own line, indented, when there is more than one.
- Indent `JOIN` and `ON` under the `FROM` they belong to.
- Leave identifiers, string literals, and the query's meaning untouched.
