# Telemetry anomaly platform

Telemetry ingestion and anomaly alerting: a simulator publishes device readings to Kafka, a Spring Boot service validates and stores them, and a FastAPI service applies rules and pushes alerts to a live web view.

> Work in progress. Inspired by the ground-control domain; all data and design here are invented and my own.

Architecture, failure modes, test notes and a measured load figure will be added as the milestones land.
