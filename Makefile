.PHONY: build clean config dev down logs logs-backend logs-frontend migrate ps seed smoke test up

COMPOSE := docker compose -f compose.yaml

config:
	$(COMPOSE) config --quiet

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up --build --detach --wait

dev: up

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

logs-frontend:
	$(COMPOSE) logs -f --tail=100 frontend

logs-backend:
	$(COMPOSE) logs -f --tail=100 api-gateway disaster-service

ps:
	$(COMPOSE) ps

test:
	docker build --quiet --target test -t cusol-api-gateway-test services/api-gateway
	docker run --rm cusol-api-gateway-test
	docker build --quiet --target test -t cusol-disaster-service-test services/disaster-service
	docker run --rm cusol-disaster-service-test

migrate:
	@echo "Aplicando esquema (infra/postgres/init) a la base local existente"
	@for f in infra/postgres/init/*.sql; do \
		echo "  -> $$f"; \
		$(COMPOSE) exec -T disasters-db sh -c 'psql -v ON_ERROR_STOP=1 -q -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"' < $$f || exit 1; \
	done

seed:
	@echo "Aplicando datos semilla sintéticos (infra/postgres/seed) a la base local"
	@for f in infra/postgres/seed/*.sql; do \
		echo "  -> $$f"; \
		$(COMPOSE) exec -T disasters-db sh -c 'psql -v ON_ERROR_STOP=1 -q -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"' < $$f || exit 1; \
	done

smoke:
	./scripts/smoke-test.sh

clean:
	@echo "Eliminando contenedores y volumen local cusol-disasters-data"
	$(COMPOSE) down --volumes --remove-orphans
