# Stage 1: Build Go binary
FROM golang:1.22-alpine AS go-builder

WORKDIR /build
COPY go.mod go.sum ./
RUN go mod download

COPY cmd/ cmd/
COPY internal/ internal/
RUN CGO_ENABLED=0 GOOS=linux go build -o /devai ./cmd/devai

# Stage 2: Python ML service
FROM python:3.12-slim AS final

# Install git (needed for git operations)
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Copy Go binary
COPY --from=go-builder /devai /usr/local/bin/devai

# Install Python ML service
WORKDIR /app
COPY ml/pyproject.toml ./
RUN pip install --no-cache-dir .

COPY ml/devai_ml/ ./devai_ml/
COPY proto/ ./proto/

# Create state directory
RUN mkdir -p /data/state

ENV DEVAI_STATE_DIR=/data/state
ENV DEVAI_ML_MODEL=minilm-l6

EXPOSE 8080
EXPOSE 50051

# Default: start the Go binary which manages the ML sidecar
ENTRYPOINT ["devai"]
CMD ["server", "start", "--state-dir", "/data/state"]
