package com.guimineve.telemetry.ingestion;

import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.util.backoff.ExponentialBackOff;

@Configuration
public class KafkaConfig {

    @Bean
    NewTopic rawTopic(TelemetryProperties p) {
        return TopicBuilder.name(p.topics().raw()).partitions(p.partitions()).replicas(1).build();
    }

    @Bean
    NewTopic validatedTopic(TelemetryProperties p) {
        return TopicBuilder.name(p.topics().validated()).partitions(p.partitions()).replicas(1).build();
    }

    @Bean
    NewTopic dlqTopic(TelemetryProperties p) {
        return TopicBuilder.name(p.topics().dlq()).partitions(p.partitions()).replicas(1).build();
    }

    /**
     * A failing batch (for example MongoDB down) is retried forever with capped exponential backoff.
     * Nothing is acked meanwhile, so the backlog stays in Kafka. Bad *messages* never reach this
     * handler: they are routed to the DLQ by the service.
     */
    @Bean
    DefaultErrorHandler errorHandler() {
        ExponentialBackOff backOff = new ExponentialBackOff(1_000, 2.0);
        backOff.setMaxInterval(30_000);
        return new DefaultErrorHandler(backOff);
    }
}
