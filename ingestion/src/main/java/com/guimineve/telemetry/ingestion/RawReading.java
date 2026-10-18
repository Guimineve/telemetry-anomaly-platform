package com.guimineve.telemetry.ingestion;

import java.time.Instant;

/** A reading that passed schema validation. */
public record RawReading(
        String id, String deviceId, String metric, double value, String unit, Instant timestamp) {}
