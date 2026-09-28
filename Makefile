PYTHON ?= .venv/bin/python
FLAVOR ?= light

.PHONY: setup tools assets style plan check test build catalog publish clean

setup: .venv tools assets style

.venv: pyproject.toml
	python3 -m venv .venv
	.venv/bin/pip install -q -e '.[publish]'
	touch .venv

tools:
	scripts/fetch-tools.sh

assets:
	scripts/fetch-assets.sh

style: assets
	cd style && npm install --silent && node gen.mjs $(FLAVOR)

# regions.toml from regions/plan.toml and Geofabrik's index
plan: .venv
	$(PYTHON) -m meshmaps plan

check: .venv
	$(PYTHON) -m meshmaps check

test: .venv
	$(PYTHON) -m unittest discover -s tests

# make build REGION=us-washington
build: .venv
	$(PYTHON) -m meshmaps build $(REGION) --flavor $(FLAVOR)

catalog: .venv
	$(PYTHON) -m meshmaps catalog

# make publish REGION=us-washington  (needs the R2_* variables; see README)
publish: .venv
	$(PYTHON) -m meshmaps publish $(REGION)

clean:
	rm -rf build/work dist
