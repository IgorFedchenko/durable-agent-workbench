.PHONY: demo sandbox build quality test eval capture secrets
UV ?= uv
# Optional generic command prefix, e.g. JOB="your-local-job-runner --".
JOB ?=

demo:
	$(JOB) bash scripts/demo.sh
sandbox build:
	$(JOB) python3 scripts/build_sandbox.py
quality:
	$(UV) run ruff check src scripts tests
	$(UV) run ruff format --check src scripts tests
	$(UV) run mypy src
secrets:
	gitleaks git --redact --no-banner
	gitleaks dir --redact --no-banner .
test:
	$(JOB) $(UV) run pytest --junitxml=.workbench/test-results/all.xml
eval:
	$(JOB) $(UV) run workbench eval --output evals/benchmark.json
capture:
	$(JOB) $(UV) run python scripts/capture_demo.py
