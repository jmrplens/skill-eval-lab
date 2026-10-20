# skill-eval-lab

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/jmrplens/skill-eval-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/jmrplens/skill-eval-lab/actions/workflows/ci.yml)

A small, provider-neutral lab for two questions about an
[Agent Skill](https://agentskills.io/specification):

1. **Does it load when it should, and stay out of the way when it should not?**
   (the trigger test)
2. **Does it change what a model answers, for the better?**
   (the scenario test, graded against an answer key)

It measures both across several model providers at once, so you can see a
skill's effect on a strong model and a weak one side by side.

## How it models a skill

The lab does not rely on any client's native skill support. Each model is given
a **catalog** of the installed skills in its system prompt (each skill's name,
its "when to use" description, and the path of its `SKILL.md`) and two read-only
tools, `read_file` and `list_dir`, scoped to the skill library. The model
decides for itself whether a request matches a skill, reads that skill's
`SKILL.md` and the files it points to, and then answers. This is the lowest
common denominator: it works for any chat model with tool calling, and it is a
faithful stand-in for how an assistant without built-in skills would use the
Agent Skills format.

A run has four phases:

- **trigger** drives the catalog with prompts that should and should not match
  the skill under test, and records which skills the model actually opened.
- **scenarios** answers realistic requests twice, once with the skill installed
  and once without it, so the two answers can be compared.
- **judge** grades each scenario answer against the suite's answer key, blind to
  the model and the condition that produced it, through a single judge model
  forced to answer with a structured tool call.
- **report** summarises triggering accuracy and the with/without score delta per
  model.

Every answer is a self-contained file under the run directory, so a stopped run
resumes where it stopped, and the whole thing runs inside a `python:3.13-slim`
container with no dependencies beyond the Python standard library.

## Requirements

- Docker.
- An API key for at least one provider (see `.env.example`).

## Quick start

```shell
git clone https://github.com/jmrplens/skill-eval-lab
cd skill-eval-lab
cp .env.example .env            # then fill in the keys you have
OUT=runs/example MODELS=haiku-5.5 ./pipeline.sh
tail -f runs/example/lab.log    # follow the run
```

`pipeline.sh` runs trigger, scenarios, judge and report in one detached
container against the bundled **example** suite (`suites/example`), which tests a
tiny `conventional-commits` skill. Point it at your own suite and skill library
with `SUITE=` and `SKILLS=`.

To run one phase at a time, use `lab.sh`:

```shell
OUT=runs/example ./lab.sh trigger   --models haiku-5.5
OUT=runs/example ./lab.sh scenarios --models haiku-5.5
OUT=runs/example ./lab.sh judge
OUT=runs/example ./lab.sh report
```

### Keys and models

The five provider keys are read from `ENV_FILE` (default `./.env`) into the
container by name, so no value ever appears on a command line or in output. One
of the five, `openrouter`, is an OpenAI-compatible gateway to many providers'
models, keyed by `OPENROUTER_API_KEY`, whose model ids take the `provider/slug`
form it routes by.

`lab/models.json` is a map of provider to the array of models for that provider:

```json
{
  "anthropic": [{"id": "haiku-5.5", ...}, {"id": "sonnet-5.5", ...}, {"id": "opus-5.5", ...}],
  "google":    [{"id": "gemini-flash", ...}]
}
```

`--models` (or `MODELS=`) picks what to run, as a comma-separated list whose
tokens are any mix of:

- a model id (`haiku-5.5`);
- a provider, meaning every model in its array (`anthropic`);
- a provider with a `+`-separated subset (`anthropic:haiku-5.5+sonnet-5.5`).

So `MODELS=anthropic:haiku-5.5` runs Claude Haiku alone and `MODELS=anthropic`
(the same as `MODELS=haiku-5.5,sonnet-5.5,opus-5.5`) runs Haiku, Sonnet and
Opus. With nothing selected, every model in the file runs, so set `--models` to
the ones whose provider key you have, or a model with an empty key errors when
it is called. Add a model to a provider's array, or a whole provider by adding a
file under `lab/providers/` (see below).

**Adding a provider is adding one file** under `lab/providers/`. Every module in
that package is imported on load, so a file like `lab/providers/mistral.py` that
defines a class and registers it is all the wiring there is:

```python
from providers import Adapter, register  # or OpenAICompatible for an OpenAI-style API

@register("mistral")
class Mistral(Adapter):
    slots = 3                       # calls in flight at once
    def build(self, system, msgs, tools, tool_choice): ...   # history -> the API's request
    def parse(self, data): ...                               # the API's answer -> a Response
```

An OpenAI-compatible endpoint is even shorter: subclass `OpenAICompatible` and
set `url`, `key_var`, and (if they differ) `max_tokens_field` and `max_tokens`.
The agent and the judge only ever call `adapter_for(model, budget)` and the
shared `Adapter.call`, so nothing upstream changes when a provider is added.

### Your own providers, models and prices

You pick which providers to use and which models in each with a **catalog
file**, and discover the available models and prices rather than typing them.

**Discover.** `run.py discover` lists each provider's models through its own
`/models` API and autofills prices from OpenRouter's public models API (matched
by exact model name), writing a `providers.yaml` you can edit:

```shell
OUT=runs/x ./lab.sh discover                    # writes runs/x/providers.yaml
```

It needs each provider's key in your `.env` for the listing; OpenRouter needs
none. A model OpenRouter has no price for is written as a commented `price`
TODO. Prices are OpenRouter's figures, so verify them against the provider's own
pricing page; the budget refuses a selected model that has no price, so an
unresolved one never silently drives spending.

**The catalog file** is passed with `MODELS_FILE=` to **both** `pipeline.sh` and
`lab.sh` (`--models-file` is only the flag those wrappers forward to the engine
inside the container, so always use the env var):

```shell
OUT=runs/x MODELS_FILE=providers.example.yaml ./pipeline.sh
OUT=runs/x MODELS_FILE=providers.example.yaml ./lab.sh scenarios --models anthropic
```

A provider's value is either the list of its models (the compact shape, like
`lab/models.json`) or a block `{api_key_env|api_key, models: [...]}` that says
where the provider's key comes from: a `.env` variable name (`api_key_env`, the
default) or an inline `api_key`. See `providers.example.yaml` for the full shape
and `models.example.yaml` for a minimal re-price. A model entry is `{id, model,
price}`.

The file is merged **over** the built-in `lab/models.json`: a new id is added,
and an id already there has its top-level fields replaced (so to change one
figure of `price` you give the whole `price` object). It may be JSON or YAML; a
`.yaml`/`.yml` file needs PyYAML, which the lab adds to the container only for
that extension, so a JSON catalog stays dependency-free. A brand-new provider id
also needs its one file under `lab/providers/` (above), since the adapter is
resolved by that name.

### Budget

Every paid call reserves its worst-case cost against a ledger in the run
directory before it is sent, and the run stops cleanly when the cap is reached.
Set the cap with `CAP=` (US dollars) for `pipeline.sh`, or `--cap` for `lab.sh`.
Prices live in `lab/models.json` (and `prices.json` records where each figure
came from). Check the current prices against each provider's own page before you
rely on the budget.

## Writing a suite

A suite is a directory with two parts:

```text
suites/<name>/
  inputs/
    suite.json          names the skill under test and what the judge needs
    trigger-prompts.md  "- T01: ..." lines; should-load and should-not-load
    scenarios.md        "## S1 ..." sections; realistic requests
  key/
    scenario-key.md     "## S1 ..." sections with numbered Must / Must not points
```

`suite.json` ties them together:

```json
{
  "skill": "conventional-commits",
  "key_file": "scenario-key.md",
  "triggers": {
    "should": ["T01", "T02", "T03"],
    "should_not": ["T04", "T05"]
  },
  "judge_sources": {
    "S1": ["SKILL.md"],
    "S2": ["SKILL.md"]
  }
}
```

- `triggers.should` are the prompt ids where the skill under test must load;
  `should_not` are the ids where it must not (a `soft_not` list is allowed for
  borderline prompts that are reported but not counted as failures).
- `judge_sources` names, per scenario, the skill files the judge is shown
  alongside the request and the key, so it grades against the skill's own words.
- The skill library lives under `skills/` (override with `SKILLS=`): one
  directory per skill, each with a `SKILL.md` whose frontmatter has a `name` and
  a `description`.

The bundled `suites/example` and `skills/` are a complete, runnable example.

## Layout

```text
lab.sh              one phase in a container
pipeline.sh         all four phases in one detached container
lab/                the engine (standard library only)
  run.py            the CLI: smoke, trigger, scenarios, judge, report
  agent.py          the skill catalog, the read-only tools, the tool loop
  providers/        one module per provider; __init__ holds the base and registry
    __init__.py     Adapter, OpenAICompatible, register, adapter_for
    anthropic.py    openai.py  google.py  qwen.py  openrouter.py
  judge.py          blind grading against the answer key
  report.py         triggering accuracy and with/without deltas
  common.py         budget ledger, usage and cost accounting, HTTP
  models.json       the models and their prices
  trigger_summary.py
models.example.yaml a minimal MODELS_FILE catalog (re-price, add a model)
providers.example.yaml  a full MODELS_FILE catalog (providers, models, keys)
prices.json         where each price figure came from
suites/example/     a runnable example suite
skills/             the example skill library
```

## Not affiliated

This is an independent tool, provided as is under the MIT license (see
`LICENSE`). Model names, provider endpoints and prices in `lab/models.json` and
`prices.json` are there as configuration and examples; check each provider's own
documentation for current models and pricing. The example skill is illustrative
and not guidance about git.

The source of truth for this repository is
[github.com/jmrplens/skill-eval-lab](https://github.com/jmrplens/skill-eval-lab),
mirrored at
[gitlab.com/jmrp/skill-eval-lab](https://gitlab.com/jmrp/skill-eval-lab).
