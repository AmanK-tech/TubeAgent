PYTHONPATH?=./src
PORT?=8000
HOST?=127.0.0.1
WS_PING_INTERVAL?=20
WS_PING_TIMEOUT?=20

.PHONY: api
api:
	PYTHONPATH=$(PYTHONPATH) uvicorn app.main:app --reload --port $(PORT) --host $(HOST) --ws-ping-interval $(WS_PING_INTERVAL) --ws-ping-timeout $(WS_PING_TIMEOUT)

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
