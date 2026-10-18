package com.guimineve.telemetry.ingestion;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.common.header.Header;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.bson.Document;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.MongoDBContainer;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.kafka.KafkaContainer;
import org.testcontainers.utility.DockerImageName;

/** End to end through real Kafka and MongoDB: raw -> (validated | dlq) + Mongo. */
@SpringBootTest(properties = {"telemetry.partitions=1"})
@Testcontainers
class IngestionFlowIT {

    static final KafkaContainer KAFKA = new KafkaContainer(DockerImageName.parse("apache/kafka:3.9.1"));
    static final MongoDBContainer MONGO = new MongoDBContainer(DockerImageName.parse("mongo:7.0"));

    @BeforeAll
    static void start() {
        KAFKA.start();
        MONGO.start();
    }

    @AfterAll
    static void stop() {
        KAFKA.stop();
        MONGO.stop();
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry r) {
        r.add("spring.kafka.bootstrap-servers", KAFKA::getBootstrapServers);
        r.add("spring.data.mongodb.uri", () -> MONGO.getReplicaSetUrl("telemetry"));
    }

    @Autowired KafkaTemplate<String, String> kafka;
    @Autowired MongoTemplate mongo;

    static String reading(String id) {
        return """
                {"id":"%s","deviceId":"sensor-001","metric":"temperature","value":41.2,\
                "unit":"celsius","timestamp":"2026-01-01T10:00:00.123Z"}""".formatted(id);
    }

    @Test
    void validReadingIsStoredAndForwarded() {
        String id = UUID.randomUUID().toString();
        kafka.send("telemetry.raw", "sensor-001", reading(id));

        await().atMost(Duration.ofSeconds(30)).untilAsserted(() -> {
            Document doc = mongo.findById(id, Document.class, "readings");
            assertThat(doc).isNotNull();
            assertThat(doc.getString("deviceId")).isEqualTo("sensor-001");
            assertThat(doc.getDouble("value")).isEqualTo(41.2);
        });
        assertThat(consumeUntil("telemetry.validated", r -> r.value().contains(id)))
                .hasSize(1);
    }

    @Test
    void invalidMessageGoesToDlqWithReasonAndIsNotStored() {
        String payload = "{\"deviceId\":\"sensor-001\"}";
        kafka.send("telemetry.raw", "sensor-001", payload);

        List<ConsumerRecord<String, String>> dlq =
                consumeUntil("telemetry.dlq", r -> payload.equals(r.value()));
        assertThat(dlq).hasSize(1);
        Header reason = dlq.get(0).headers().lastHeader("reason");
        assertThat(new String(reason.value(), StandardCharsets.UTF_8)).startsWith("schema violation");
        assertThat(dlq.get(0).headers().lastHeader("origTopic")).isNotNull();
    }

    @Test
    void redeliveredReadingIsStoredOnce() {
        String id = UUID.randomUUID().toString();
        kafka.send("telemetry.raw", "sensor-001", reading(id));
        kafka.send("telemetry.raw", "sensor-001", reading(id));

        // both copies are forwarded (at-least-once) ...
        assertThat(consumeUntil("telemetry.validated", r -> r.value().contains(id), 2)).hasSize(2);
        // ... but storage is idempotent
        assertThat(mongo.getCollection("readings").countDocuments(new Document("_id", id))).isEqualTo(1);
    }

    private static List<ConsumerRecord<String, String>> consumeUntil(
            String topic, java.util.function.Predicate<ConsumerRecord<String, String>> match) {
        return consumeUntil(topic, match, 1);
    }

    private static List<ConsumerRecord<String, String>> consumeUntil(
            String topic, java.util.function.Predicate<ConsumerRecord<String, String>> match, int want) {
        Map<String, Object> cfg = Map.of(
                ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, KAFKA.getBootstrapServers(),
                ConsumerConfig.GROUP_ID_CONFIG, "it-" + UUID.randomUUID(),
                ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest",
                ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class,
                ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        List<ConsumerRecord<String, String>> found = new ArrayList<>();
        try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(cfg)) {
            consumer.subscribe(List.of(topic));
            await().atMost(Duration.ofSeconds(30)).until(() -> {
                consumer.poll(Duration.ofMillis(500)).forEach(r -> {
                    if (match.test(r)) found.add(r);
                });
                return found.size() >= want;
            });
        }
        return found;
    }
}
