.PHONY: check format lint typecheck

check: format lint typecheck

format:
	uvx ruff format .

lint:
	uvx ruff check .

typecheck:
	uvx ty check .
