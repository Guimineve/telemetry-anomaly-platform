package com.guimineve.telemetry.ingestion;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "telemetry")
public record TelemetryProperties(Topics topics, int partitions, int retentionDays) {
    public record Topics(String raw, String validated, String dlq) {}
}
