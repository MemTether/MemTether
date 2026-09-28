# Contributing to MemTether

Thank you for considering contributing! This document explains how to set up your environment and submit changes.

## Getting Started

### 1. Fork & Clone

```bash
git clone https://github.com/YOUR_USERNAME/MemTether.git
cd MemTether
```

### 2. Set Up Environment

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# or: .venv\Scripts\activate  # Windows

pip install -e ".[vector]"
pip install pytest pytest-cov ruff pre-commit
```

### 3. Install Pre-commit Hooks

```bash
pre-commit install
```

This runs lint checks (ruff) and formatting before every commit.

## Development Workflow

### 1. Create a Branch

```bash
git checkout -b feature/your-feature-name
# or: git checkout -b fix/issue-description
```

### 2. Make Changes

- Write code with clear docstrings
- Add tests for new functionality in `tests/`
- Keep functions focused — one thing per function
- Follow existing patterns (look at `gateway.py` for write patterns, `memsearch.py` for retrieval patterns)

### 3. Run Tests

```bash
# Full test suite
make test
# or:
python -m pytest tests/ -v
python regression_test.py
python test_pii_roundtrip.py

# Lint
make lint
# or:
ruff check .
```

**All tests must pass before you submit a PR.**

### 4. Commit

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(scope): add new exchange adapter for XYZ
fix(bench): correct denominator calculation in hard_bench
docs(readme): update install instructions
refactor(memsearch): extract entity boost into separate function
test(exchange): add roundtrip test for COGX adapter
chore(deps): bump numpy to >=1.24
```

### 5. Push & Create PR

```bash
git push origin feature/your-feature-name
```

Then open a Pull Request on GitHub. Please fill in the PR template.

## Code Style

- **Line length**: 120 chars max (ruff-configured)
- **Python version**: 3.10+
- **Type hints**: encouraged but not enforced
- **Docstrings**: required for public functions
- **Encoding**: UTF-8, LF line endings (enforced by pre-commit)

## Testing Guidelines

- New features must include tests in `tests/`
- Bug fixes must include a regression test
- Use the fixtures in `tests/conftest.py` (especially `tmp_db`)
- Tests should be independent (no shared state between tests)
- Use `pytest.mark.slow` for tests that take >5 seconds

## Project Structure

```
gateway.py          # Write pipeline (sole entry point)
memsearch.py        # Retrieval pipeline (hybrid search)
governance.py       # Conflict detection, retire, bitemporal
hubguard.py         # Concurrency locks
memtether_exchange.py  # Exchange Schema v2
mem0_exchange.py    # Mem0 adapter
cogx_exchange.py    # COGX (cognee) adapter
tether_connect.py   # Client auto-connect
memtether.py        # CLI entry point
mcp_server.py       # MCP server
tests/              # Test suite
scripts/            # Utility scripts
docs/               # Documentation
```

## Adding a New Exchange Adapter

If you want to add an adapter for a memory system not yet supported:

1. Read `docs/memory_exchange_schema.md` to understand the protocol
2. Look at `mem0_exchange.py` as a reference implementation
3. Create `yourmem_exchange.py` following the same pattern:
   - `<name>_to_exchange()`: import from your system
   - `exchange_to_<name>()`: export to your system
4. Add the module name to `py-modules` in `pyproject.toml`
5. Write tests in `tests/test_yourmem_exchange.py`
6. Update the README comparison table

## Reporting Issues

- **Bug**: Use the bug report template. Include OS, Python version, steps to reproduce.
- **Feature**: Use the feature request template. Explain the use case.
- **Question**: Use the question template or start a Discussion.

## License

By contributing, you agree that your contributions will be licensed under the Apache-2.0 License.
