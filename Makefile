# Developer convenience targets. The lab itself runs in a container (see
# lab.sh / pipeline.sh); these targets are the offline checks CI runs, plus a
# shortcut to the example run. No target needs an API key except `run`.

.PHONY: check compile providers models shell run clean

## check: every offline check CI runs (no API key, no network)
check: compile providers models shell
	@echo "all checks passed"

## compile: byte-compile every engine module
compile:
	python3 -I -m py_compile lab/*.py lab/providers/*.py

## providers: the provider registry resolves to the expected adapters
providers:
	python3 -I -c "import sys; sys.path.insert(0, 'lab'); from providers import ADAPTERS; \
	got = sorted(ADAPTERS); exp = sorted(['anthropic', 'openai', 'openai-responses', 'google', 'qwen', 'openrouter']); \
	assert got == exp, (got, exp); print('providers:', got)"

## models: the built-in model catalog loads and is well-formed
models:
	python3 -I -c "import sys; sys.path.insert(0, 'lab'); from common import load_models; \
	m = load_models(); assert len(m) >= 7, len(m); \
	assert all('provider' in v and 'price' in v for v in m.values()); print('models:', sorted(m))"

## shell: the wrappers parse and lint
shell:
	bash -n lab.sh pipeline.sh
	shellcheck lab.sh pipeline.sh

## run: the bundled example suite against Haiku (needs ANTHROPIC_API_KEY in .env)
run:
	OUT=runs/example MODELS=haiku-5.5 ./pipeline.sh

## clean: remove local run outputs and bytecode
clean:
	rm -rf runs out lab/__pycache__ lab/providers/__pycache__
