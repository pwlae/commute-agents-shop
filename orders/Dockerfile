# Build context: the repository root (needs libs/http-ts).
FROM node:22.23.5-alpine AS build
WORKDIR /app
COPY libs/http-ts/package.json libs/http-ts/package-lock.json libs/http-ts/tsconfig.json libs/http-ts/
COPY libs/http-ts/src libs/http-ts/src
RUN cd libs/http-ts && npm ci --ignore-scripts && npm run build
COPY orders/package.json orders/package-lock.json orders/tsconfig.json orders/
RUN cd orders && npm ci
COPY orders/src orders/src
RUN cd orders && npm run build && npm prune --omit=dev

FROM node:22.23.5-alpine
ENV NODE_ENV=production KAFKAJS_NO_PARTITIONER_WARNING=1
WORKDIR /app
COPY --from=build /app/libs/http-ts/package.json libs/http-ts/
COPY --from=build /app/libs/http-ts/dist libs/http-ts/dist
COPY --from=build /app/orders/package.json orders/
COPY --from=build /app/orders/node_modules orders/node_modules
COPY --from=build /app/orders/dist orders/dist
WORKDIR /app/orders
USER node
EXPOSE 3001
CMD ["node", "--max-old-space-size=80", "dist/main.js"]
