.PHONY: install lint test eval demo seed serve check clean

install:
	uv sync --extra llm --extra dev

lint:
	uv run ruff check src tests

test:
	uv run pytest

eval:
	uv run mte eval

demo:
	uv run mte demo

seed:
	uv run mte seed

serve:
	uv run mte serve --reload

check: lint test eval

clean:
	rm -rf .pytest_cache .ruff_cache data/demo
