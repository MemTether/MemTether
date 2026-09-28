.PHONY: test lint build publish clean demo check

# Run full test suite
test:
	python -m pytest tests/ -v --tb=short
	python regression_test.py
	python test_pii_roundtrip.py

# Lint
lint:
	ruff check . --select E9,F63,F7,F82 --ignore E501
	ruff format --check .

# Format code
format:
	ruff format .

# Build wheel + sdist
build:
	python -m build --outdir dist

# Check build artifacts
check-dist:
	twine check dist/*

# Upload to PyPI (requires TWINE_PASSWORD env var)
publish:
	twine upload dist/* --username __token__ --non-interactive

# Generate demo database
demo:
	python scripts/make_demo_db.py

# Run self-check
check:
	python hub_selfcheck.py

# Clean build artifacts
clean:
	rm -rf dist/ build/ *.egg-info __pycache__ .pytest_cache .ruff_cache
	find . -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
