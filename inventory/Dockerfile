# Build from the repo root: docker build -f inventory/Dockerfile .
FROM golang:1.27.4-alpine3.24 AS build
WORKDIR /src/inventory
COPY inventory/go.mod inventory/go.sum ./
RUN go mod download
COPY inventory/ ./
ARG BAD_SKU_CASE=0
RUN case=""; [ "$BAD_SKU_CASE" = 1 ] && case=lower; \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w \
    -X github.com/pwlae/commute-agents-shop/inventory/internal/consumer.skuCase=$case" \
    -o /out/inventory ./cmd/inventory

FROM alpine:3.24.4
RUN adduser -D -H -u 10001 app
COPY --from=build /out/inventory /usr/local/bin/inventory
USER app
EXPOSE 8200
HEALTHCHECK --interval=5s --timeout=3s --retries=20 \
    CMD ["inventory", "healthcheck"]
ENTRYPOINT ["inventory"]
