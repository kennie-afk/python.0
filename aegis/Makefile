# Aegis demo helpers. Everything honours the ports in .env (AEGIS_PORT, AEGIS_CONSOLE_PORT,
# POSTGRES_PORT) and COMPOSE_PROJECT_NAME, so a demo can sit beside other stacks.

COMPOSE ?= docker compose

.PHONY: up demo reseed down test

up:
	$(COMPOSE) up -d --build

# Start the stack, then create and fill a demo tenant through the real API. Prints a key once.
demo: up
	$(COMPOSE) exec -e AEGIS_ALLOW_DEMO_SEED=true api python -m aegis.demo

# Wipe the demo tenant and build it again from scratch.
reseed:
	$(COMPOSE) exec -e AEGIS_ALLOW_DEMO_SEED=true api python -m aegis.demo --reset

down:
	$(COMPOSE) down

test:
	uv run pytest && uv run ruff check . && uv run mypy
