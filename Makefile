PROMETHEUS_IMAGE := prom/prometheus:v3.15.0
ALERTMANAGER_IMAGE := prom/alertmanager:v0.34.1
PYTHON_IMAGE := python:3.13-slim
COMPOSE := docker compose

.DEFAULT_GOAL := help
.PHONY: help init preflight up down restart ps logs check test-alert reload validate test-rules test lint tunnel

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

init: ## Create .env from .env.example with a random Grafana password
	@if [ -f .env ]; then echo ".env already exists, not touching it"; exit 0; fi; \
	cp .env.example .env && chmod 600 .env && \
	pw=$$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24) && \
	sed -i "s/^GRAFANA_ADMIN_PASSWORD=.*/GRAFANA_ADMIN_PASSWORD=$$pw/" .env && \
	sed -i "s/^SERVER_NAME=.*/SERVER_NAME=$$(hostname -f 2>/dev/null || hostname)/" .env && \
	echo "Created .env (Grafana password generated). Now set WHM_API_TOKEN and Telegram in .env"

preflight: ## Check the server (Docker, ports, Exim spool, WHM token)
	@bash scripts/preflight.sh

up: ## Build and start the stack
	$(COMPOSE) up -d --build

down: ## Stop the stack (data volumes are kept)
	$(COMPOSE) down

restart: ## Restart all services
	$(COMPOSE) restart

ps: ## Show container status
	$(COMPOSE) ps

logs: ## Follow logs (make logs s=whm-exporter for one service)
	$(COMPOSE) logs -f --tail=100 $(s)

check: ## Run every collector once against WHM and print the result
	$(COMPOSE) run --rm --no-deps whm-exporter --check

test-alert: ## Send a test alert through Alertmanager to Telegram (arrives in ~30s)
	$(COMPOSE) exec alertmanager amtool alert add alertname=WHMTestAlert severity=warning \
		server="$$(grep -E '^SERVER_NAME=' .env | cut -d= -f2-)" \
		--annotation='summary="Test alert from whm-monitor, Telegram works"' \
		--alertmanager.url=http://127.0.0.1:9093

reload: ## Reload Prometheus rules/config and Alertmanager config without restart
	$(COMPOSE) kill -s SIGHUP prometheus alertmanager

validate: ## Validate Prometheus and Alertmanager config with promtool/amtool
	docker run --rm -v "$(CURDIR)/prometheus:/etc/prometheus:ro" --entrypoint promtool \
		$(PROMETHEUS_IMAGE) check config /etc/prometheus/prometheus.yml
	docker run --rm -v "$(CURDIR)/alertmanager:/etc/alertmanager:ro" --entrypoint amtool \
		$(ALERTMANAGER_IMAGE) check-config /etc/alertmanager/alertmanager.yml /etc/alertmanager/alertmanager-noop.yml

test-rules: ## Unit-test the alert rules (prometheus/tests/rules_test.yml)
	docker run --rm -v "$(CURDIR)/prometheus:/etc/prometheus:ro" -w /etc/prometheus/tests \
		--entrypoint promtool $(PROMETHEUS_IMAGE) test rules rules_test.yml

test: ## Run the exporter's unit tests in a container
	docker run --rm -v "$(CURDIR)/exporter:/src" -w /src $(PYTHON_IMAGE) \
		sh -c "pip install -q -r requirements-dev.txt && python -m pytest -p no:cacheprovider"

lint: ## Lint the exporter with ruff
	docker run --rm -v "$(CURDIR)/exporter:/src" -w /src $(PYTHON_IMAGE) \
		sh -c "pip install -q ruff && ruff check . && ruff format --check ."

tunnel: ## Print the SSH command that opens Grafana/Prometheus/Alertmanager locally
	@echo "Run this on your own computer, then open http://localhost:3000"
	@echo "  ssh -N -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 -L 9093:127.0.0.1:9093 root@$$(hostname -f 2>/dev/null || hostname)"
