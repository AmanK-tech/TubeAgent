PYTHONPATH?=./src
PORT?=5050
HOST?=127.0.0.1
# Disable server-level websocket pings by default in dev to avoid proxy-related flapping.
# Set non-zero values explicitly if you want uvicorn to send protocol pings.
WS_PING_INTERVAL?=0
WS_PING_TIMEOUT?=60

.PHONY: api
api:
	PYTHONPATH=$(PYTHONPATH) uvicorn app.main:app --reload --port $(PORT) --host $(HOST) --ws-ping-interval $(WS_PING_INTERVAL) --ws-ping-timeout $(WS_PING_TIMEOUT)
