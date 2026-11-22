# commute-agents-shop

A small practice shop for the Commute Agents course: a gateway, three
services, Kafka, a saga, three Postgres databases, Prometheus and Grafana.

Work in progress. For now it runs the infrastructure only:

```sh
docker compose up -d
docker compose ps
```

- Grafana: http://localhost:3000 (anonymous viewer)
- Prometheus: http://localhost:9090

Licence: MIT (see `LICENSE`).
