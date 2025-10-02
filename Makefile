PYTHONPATH?=./src
PORT?=8000

.PHONY: api
api:
	PYTHONPATH=$(PYTHONPATH) uvicorn app.main:app --reload --port $(PORT)

.PHONY: docker-build
docker-build:
	docker build -t tubeagent-api .

.PHONY: docker-run
docker-run:
	docker run --rm -p $(PORT):8000 \
	  -e WEB_ORIGIN=http://localhost:5173 \
	  -e RUNTIME_DIR=/data/runtime \
	  -v $$(pwd)/runtime:/data/runtime \
	  tubeagent-api

