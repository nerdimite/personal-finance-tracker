.PHONY: up down restart ps logs db-up migrate seed sync-categories schema-doc backup test-db reset-ledger

# Whole stack: postgres, bot, dashboard. Rebuilds images if the code changed.
up:
	docker compose up -d --build --wait

# Stops and removes the containers. The pgdata volume (your ledger) is kept.
down:
	docker compose down

restart: down up

ps:
	docker compose ps

# make logs, or make logs S=bot
logs:
	docker compose logs -f --tail=100 $(S)

db-up:
	docker compose up -d --wait postgres

migrate:
	uv run ft-migrate

seed:
	uv run ft-seed

sync-categories:
	uv run ft-sync-categories

schema-doc:
	uv run ft-schema-doc

backup:
	./db/backup.sh

test-db:
	uv run ft-test-db

# Deletes ledger rows in the finance database, then re-seeds. Does not touch logs/ or workspace/messages/.
reset-ledger:
	@test "$(YES)" = "1" || { echo "refusing to reset the ledger. Re-run as: make reset-ledger YES=1"; exit 2; }
	uv run ft-reset-ledger --yes

.PHONY: dashboard-dev dashboard-up

dashboard-dev:
	cd dashboard && npm run dev

dashboard-up:
	docker compose up -d --build dashboard

.PHONY: bot

bot:
	uv run python -m ft_bot
