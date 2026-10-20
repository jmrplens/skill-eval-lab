# Security policy

## Supported versions

Only the latest commit on `main` is supported. There are no backports; if you
hit a problem, update to the latest version first.

## Reporting a vulnerability

Report privately through GitHub Security Advisories:
[open a draft advisory](https://github.com/jmrplens/skill-eval-lab/security/advisories/new).
Please do not open a public issue for a suspected vulnerability.

This is a small, dependency-free tool that runs locally in a container and
talks only to the model-provider endpoints you configure. Keep in mind that
your provider API keys live in `.env` (git-ignored) and are passed to the
container by name; never commit them.
