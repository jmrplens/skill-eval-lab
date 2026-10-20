# Trigger prompts for the example suite

Each line is `- T<nn>: <prompt>`. The suite's `triggers` lists which ids the
`conventional-commits` skill should load on (should) and which it must stay off
(should_not).

- T01: I just added a --verbose flag to the CLI. Write me a git commit message for it.
- T02: Help me write a commit message for a bug fix in the authentication module.
- T03: What should the commit message say for removing the deprecated v1 API endpoints?
- T04: What time zone is Tokyo in?
- T05: Format this SQL query for me: select id,name from users where active=true order by name
