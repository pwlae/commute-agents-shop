# Build context: the repo root (the gateway needs libs/http-ts).
#   docker build -f gateway/Dockerfile .
FROM node:22.22.0-alpine AS build
WORKDIR /app

COPY libs/http-ts/package.json libs/http-ts/package-lock.json libs/http-ts/tsconfig.json libs/http-ts/
COPY libs/http-ts/src libs/http-ts/src
RUN cd libs/http-ts && npm ci --no-audit --no-fund && npm run build

COPY gateway/package.json gateway/package-lock.json gateway/tsconfig.json gateway/
RUN cd gateway && npm ci --no-audit --no-fund
COPY gateway/src gateway/src
RUN cd gateway && npm run build && npm prune --omit=dev

FROM node:22.22.0-alpine
WORKDIR /app
ENV NODE_ENV=production
# node_modules/@shop/http is a link to ../libs/http-ts, so the
# same layout is kept here.
COPY --from=build /app/libs/http-ts/package.json libs/http-ts/
COPY --from=build /app/libs/http-ts/dist libs/http-ts/dist
COPY --from=build /app/gateway/package.json gateway/
COPY --from=build /app/gateway/node_modules gateway/node_modules
COPY --from=build /app/gateway/dist gateway/dist
WORKDIR /app/gateway
USER node
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --retries=20 \
  CMD wget -q -O /dev/null http://localhost:8080/healthz || exit 1
CMD ["node", "--max-old-space-size=64", "dist/main.js"]
