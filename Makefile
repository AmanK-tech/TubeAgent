PYTHONPATH?=./src
PORT?=5050
HOST?=127.0.0.1
WS_PING_INTERVAL?=20
WS_PING_TIMEOUT?=20

.PHONY: api
api:
	PYTHONPATH=$(PYTHONPATH) uvicorn app.main:app --reload --port $(PORT) --host $(HOST) --ws-ping-interval $(WS_PING_INTERVAL) --ws-ping-timeout $(WS_PING_TIMEOUT)
