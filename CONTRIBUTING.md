# Contributing

Thanks for your interest. This is a small, provider-neutral lab with one design
rule: the engine under `lab/` uses the **Python standard library only**, and a
run happens inside a `python:3.13-slim` container so there is nothing to install
on your machine but Docker.

## Before opening a pull request

Run the same checks CI runs (they need no API key and no network):

```shell
make check
```

That compiles every module, resolves the provider registry, loads the model
catalog, and lints the shell wrappers (`bash -n` and `shellcheck`). If you added
a provider, make sure its file under `lab/providers/` registers an adapter with
`@register("name")`; if you added a model, add it to `lab/models.json` (or show
it works through a `MODELS_FILE` catalog).

## Scope

- Keep `lab/` free of third-party Python dependencies. PyYAML is loaded lazily,
  only for a YAML `MODELS_FILE`, and never imported otherwise.
- The example suite under `suites/example` and the skills under `skills/` are
  illustrative; they are not guidance about git, SQL or regular expressions.
- Model names and prices in `lab/models.json` and `prices.json` are
  configuration and examples. Check each provider's own documentation for
  current models and pricing.

## Commit messages

Short, imperative subject lines are enough. Conventional-commit prefixes
(`feat:`, `fix:`, `docs:`, `chore:`) are welcome but not required.
