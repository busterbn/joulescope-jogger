# Joulescope power logger — one-command setup and control.
# Linux only (Docker USB passthrough does not work on Mac/Windows Docker Desktop).

# Use docker directly if the daemon is reachable as the current user, else sudo.
DOCKER  := $(shell docker info >/dev/null 2>&1 && echo docker || echo "sudo docker")
COMPOSE := $(DOCKER) compose

.DEFAULT_GOAL := install
.PHONY: install up down restart logs ps build clean help

## install: install Docker if missing, then build and start everything
install:
	@if ! command -v docker >/dev/null 2>&1; then \
		echo ">> Docker not found — installing..."; \
		curl -fsSL https://get.docker.com | sudo sh; \
		sudo usermod -aG docker $$(id -un) || true; \
		sudo systemctl enable --now docker || true; \
		echo ">> Docker installed."; \
	else \
		echo ">> Docker present: $$(docker --version)"; \
	fi
	@echo ">> Building and starting containers..."
	@$(COMPOSE) up -d --build
	@echo ">> Running. Grafana: http://localhost:3000  (InfluxDB: http://localhost:8086)"

## up: (re)start the stack, applying any code/config changes
up:
	@$(COMPOSE) up -d --build
	@echo ">> Up. Grafana: http://localhost:3000"

## down: stop and remove the containers (data is kept in volumes)
down:
	@$(COMPOSE) down

## restart: restart the running containers without rebuilding
restart:
	@$(COMPOSE) restart

## build: rebuild the logger image
build:
	@$(COMPOSE) build

## clean: delete ALL stored measurements (keeps the stack running)
clean:
	@echo ">> Deleting all measurements from the InfluxDB bucket..."
	@$(COMPOSE) exec -T influxdb sh -c 'influx delete \
		--bucket "$$DOCKER_INFLUXDB_INIT_BUCKET" \
		--org "$$DOCKER_INFLUXDB_INIT_ORG" \
		--token "$$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" \
		--start 1970-01-01T00:00:00Z --stop 2099-12-31T23:59:59Z'
	@echo ">> Done — all measurements cleared."

## logs: follow the logger output
logs:
	@$(COMPOSE) logs -f power-logger

## ps: show container status
ps:
	@$(COMPOSE) ps

## help: list available commands
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## /  make /'
