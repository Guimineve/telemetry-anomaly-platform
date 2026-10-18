package com.guimineve.telemetry.ingestion;

import java.util.List;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.stereotype.Component;

@Component
public class IngestionListener {

    private final IngestionService service;

    public IngestionListener(IngestionService service) {
        this.service = service;
    }

    @KafkaListener(topics = "${telemetry.topics.raw}")
    public void onBatch(List<ConsumerRecord<String, String>> batch, Acknowledgment ack) {
        service.process(batch);
        ack.acknowledge();
    }
}
