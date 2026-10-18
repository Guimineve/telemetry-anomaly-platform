package com.guimineve.telemetry.ingestion;

import java.time.Duration;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.data.domain.Sort;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.index.Index;
import org.springframework.stereotype.Component;

/** Raw data expires after the retention window; a compound index supports per-device queries. */
@Component
public class MongoIndexes {

    private static final Logger log = LoggerFactory.getLogger(MongoIndexes.class);

    private final MongoTemplate mongo;
    private final TelemetryProperties props;

    public MongoIndexes(MongoTemplate mongo, TelemetryProperties props) {
        this.mongo = mongo;
        this.props = props;
    }

    @EventListener(ApplicationReadyEvent.class)
    void ensure() {
        try {
            var ops = mongo.indexOps(IngestionService.COLLECTION);
            ops.ensureIndex(new Index()
                    .on("ingestedAt", Sort.Direction.ASC)
                    .expire(Duration.ofDays(props.retentionDays())));
            ops.ensureIndex(new Index().on("deviceId", Sort.Direction.ASC).on("timestamp", Sort.Direction.DESC));
        } catch (RuntimeException e) {
            log.warn("could not ensure mongo indexes (will retry on next start): {}", e.toString());
        }
    }
}
