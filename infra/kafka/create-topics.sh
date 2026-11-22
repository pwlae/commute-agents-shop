#!/bin/bash
# Creates the shop's Kafka topics. Safe to run again.
set -euo pipefail
BS=kafka:9092
BIN=/opt/kafka/bin
TOPICS="inventory.reserve.v1 inventory.reserved.v1
inventory.rejected.v1 inventory.release.v1
inventory.commit.v1 inventory.dlq.v1"
for t in $TOPICS; do
  $BIN/kafka-topics.sh --bootstrap-server $BS \
    --create --if-not-exists --topic "$t" \
    --partitions 3 --replication-factor 1
done
$BIN/kafka-topics.sh --bootstrap-server $BS --list
