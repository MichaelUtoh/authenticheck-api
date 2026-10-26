.PHONY: up down migrations migrate test lint fmt shell superuser
up:
	docker compose up --build
down:
	docker compose down
migrations:
	python manage.py makemigrations
migrate:
	python manage.py migrate
test:
	pytest
lint:
	ruff check .
fmt:
	ruff check --fix . && ruff format .
shell:
	python manage.py shell
superuser:
	python manage.py createsuperuser
