# Market Intelligence Dashboard — local commands. `make` is optional: each target is one documented command.
PY ?= .venv/bin/python
ABS_PY = $(abspath $(PY))
PORT ?= 3000
CHROMIUM ?=

.PHONY: setup setup-optional ingest indicators regime train forecast news briefing status morning evening weekly all dev test test-ui

setup:            ## create .venv and install the ML stack
	uv venv .venv --python python3 || python3 -m venv .venv
	uv pip install --python $(PY) -r requirements.txt || $(PY) -m pip install -r requirements.txt

setup-optional:   ## optional: anthropic (briefing --llm) + playwright (UI smoke)
	uv pip install --python $(PY) -r requirements-optional.txt || $(PY) -m pip install -r requirements-optional.txt

ingest:           ## price collector + Fed macro + Toss closes
	$(PY) scripts/mi_cli.py ingest

indicators regime: ## regime / risk / macro exports
	$(PY) scripts/mi_cli.py regime

train:            ## licence gate + walk-forward validation
	$(PY) scripts/mi_cli.py train

forecast:         ## resolve outcomes + append live forecasts
	$(PY) scripts/mi_cli.py forecast

news:
	$(PY) scripts/mi_cli.py news

briefing:         ## rule-based Korean briefing (add ARGS=--llm for the paid, opt-in rewrite)
	$(PY) scripts/mi_cli.py briefing $(ARGS)

status:
	$(PY) scripts/mi_cli.py status

morning evening weekly all:
	$(PY) scripts/mi_cli.py $@

dev:              ## local server (127.0.0.1, allowlist, Host check, CSP): http://localhost:3000
	python3 scripts/serve.py --port $(PORT)

test:             ## offline unit tests (collector, engine, CLI, UI)
	cd scripts && python3 test_data.py
	cd scripts && $(ABS_PY) -m unittest test_mi_forecasting test_mi_analysis test_mi_cli
	node --check app.js
	node scripts/test_ui.js
	node --test scripts/test_intel.mjs

test-ui:          ## headless browser smoke test (needs playwright in .venv)
	MI_CHROMIUM=$(CHROMIUM) $(PY) scripts/ui_smoke.py
