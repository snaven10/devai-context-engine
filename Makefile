.PHONY: build build-go build-ml test test-go test-ml lint proto clean docker docker-up docker-down install dev

# Go
GO_BIN = devai
GO_SRC = ./cmd/devai

# Python
ML_DIR = ml
VENV = $(ML_DIR)/.venv

# Default target
all: build

## Build

build: build-go build-ml

build-go:
	go build -o $(GO_BIN) $(GO_SRC)

build-ml:
	cd $(ML_DIR) && pip install -e .

## Test

test: test-go test-ml

test-go:
	go test ./... -v -count=1

test-ml:
	cd $(ML_DIR) && python -m pytest tests/ -v

## Lint

lint: lint-go lint-ml

lint-go:
	go vet ./...

lint-ml:
	cd $(ML_DIR) && ruff check devai_ml/

## Proto

proto:
	python -m grpc_tools.protoc \
		-I proto/ \
		--python_out=$(ML_DIR)/devai_ml/proto/ \
		--grpc_python_out=$(ML_DIR)/devai_ml/proto/ \
		proto/ml_service.proto

## Docker

docker:
	docker build -t devai:latest .

docker-up:
	docker compose up -d

docker-down:
	docker compose down

## Install

install: build-go
	go install $(GO_SRC)

## Development

dev: build-ml build-go
	@echo "DevAI development build complete"
	@echo "  Go binary: ./$(GO_BIN)"
	@echo "  ML service: python -m devai_ml.server"

## Clean

clean:
	rm -f $(GO_BIN)
	rm -rf $(ML_DIR)/dist $(ML_DIR)/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

## Help

help:
	@echo "DevAI Build System"
	@echo ""
	@echo "  make build       Build Go binary + install Python package"
	@echo "  make test        Run all tests"
	@echo "  make lint        Run linters"
	@echo "  make proto       Generate gRPC stubs from proto"
	@echo "  make docker      Build Docker image"
	@echo "  make docker-up   Start Docker Compose stack"
	@echo "  make docker-down Stop Docker Compose stack"
	@echo "  make install     Install Go binary"
	@echo "  make dev         Development build"
	@echo "  make clean       Clean build artifacts"
