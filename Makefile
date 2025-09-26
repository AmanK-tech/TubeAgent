PYTHONPATH?=./src
PORT?=8000

.PHONY: api
api:
	PYTHONPATH=$(PYTHONPATH) uvicorn app.main:app --reload --port $(PORT)

