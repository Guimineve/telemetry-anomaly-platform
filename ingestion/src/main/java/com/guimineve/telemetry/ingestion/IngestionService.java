package com.guimineve.telemetry.ingestion;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.springframework.data.mongodb.core.BulkOperations;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.data.mongodb.core.query.Update;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;

import static org.springframework.data.mongodb.core.query.Criteria.where;

/**
 * Processes one polled batch: valid readings are upserted into MongoDB (keyed by reading id, so
 * redelivery is idempotent) and forwarded; invalid ones go to the DLQ. The caller acks the batch
 * only if this method returns normally, which gives at-least-once delivery.
 */
@Service
public class IngestionService {

    static final String COLLECTION = "readings";
    private static final long SEND_TIMEOUT_SECONDS = 30;

    private final ReadingValidator validator;
    private final MongoTemplate mongo;
    private final KafkaTemplate<String, String> kafka;
    private final TelemetryProperties props;
    private final Counter accepted;
    private final Counter rejected;

    public IngestionService(
            ReadingValidator validator,
            MongoTemplate mongo,
            KafkaTemplate<String, String> kafka,
            TelemetryProperties props,
            MeterRegistry meters) {
        this.validator = validator;
        this.mongo = mongo;
        this.kafka = kafka;
        this.props = props;
        this.accepted = Counter.builder("ingestion.readings").tag("outcome", "accepted").register(meters);
        this.rejected = Counter.builder("ingestion.readings").tag("outcome", "rejected").register(meters);
    }

    public void process(List<ConsumerRecord<String, String>> batch) {
        List<ReadingValidator.Valid> valid = new ArrayList<>();
        List<ConsumerRecord<String, String>> validRecords = new ArrayList<>();
        List<CompletableFuture<?>> sends = new ArrayList<>();

        for (ConsumerRecord<String, String> rec : batch) {
            ReadingValidator.Result result = validator.validate(rec.value());
            if (result instanceof ReadingValidator.Valid v) {
                valid.add(v);
                validRecords.add(rec);
            } else if (result instanceof ReadingValidator.Invalid bad) {
                sends.add(sendToDlq(rec, bad.reason()));
            }
        }

        if (!valid.isEmpty()) {
            upsertAll(valid);
            for (int i = 0; i < valid.size(); i++) {
                ConsumerRecord<String, String> rec = validRecords.get(i);
                sends.add(kafka.send(props.topics().validated(), rec.key(), rec.value()));
            }
        }

        awaitAll(sends);
        accepted.increment(valid.size());
        rejected.increment(batch.size() - valid.size());
    }

    private void upsertAll(List<ReadingValidator.Valid> valid) {
        BulkOperations ops = mongo.bulkOps(BulkOperations.BulkMode.UNORDERED, COLLECTION);
        Date now = Date.from(Instant.now());
        for (ReadingValidator.Valid v : valid) {
            RawReading r = v.reading();
            ops.upsert(
                    Query.query(where("_id").is(r.id())),
                    new Update()
                            .set("deviceId", r.deviceId())
                            .set("metric", r.metric())
                            .set("value", r.value())
                            .set("unit", r.unit())
                            .set("timestamp", Date.from(r.timestamp()))
                            .set("ingestedAt", now));
        }
        ops.execute();
    }

    private CompletableFuture<?> sendToDlq(ConsumerRecord<String, String> rec, String reason) {
        ProducerRecord<String, String> out =
                new ProducerRecord<>(props.topics().dlq(), null, rec.key(), rec.value());
        out.headers()
                .add("reason", reason.getBytes(StandardCharsets.UTF_8))
                .add("origTopic", rec.topic().getBytes(StandardCharsets.UTF_8))
                .add("origPartition", Integer.toString(rec.partition()).getBytes(StandardCharsets.UTF_8))
                .add("origOffset", Long.toString(rec.offset()).getBytes(StandardCharsets.UTF_8));
        return kafka.send(out);
    }

    private static void awaitAll(List<CompletableFuture<?>> sends) {
        try {
            CompletableFuture.allOf(sends.toArray(CompletableFuture[]::new))
                    .get(SEND_TIMEOUT_SECONDS, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("interrupted while publishing", e);
        } catch (ExecutionException | TimeoutException e) {
            throw new IllegalStateException("kafka publish failed; batch will be retried", e);
        }
    }
}
