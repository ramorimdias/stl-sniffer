.PHONY: up down logs ps build restart shell-api shell-worker

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

ps:
	docker compose ps

build:
	docker compose build --pull

restart:
	docker compose restart api worker

shell-api:
	docker compose exec api sh

shell-worker:
	docker compose exec worker sh
