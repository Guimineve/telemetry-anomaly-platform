package com.guimineve.telemetry.ingestion;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SpecVersion;
import com.networknt.schema.ValidationMessage;
import java.io.IOException;
import java.io.InputStream;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.Set;
import java.util.stream.Collectors;
import org.springframework.stereotype.Component;

/** Validates a raw payload against the shared reading contract. Pure: no I/O besides loading the schema. */
@Component
public class ReadingValidator {

    public sealed interface Result permits Valid, Invalid {}

    public record Valid(RawReading reading) implements Result {}

    public record Invalid(String reason) implements Result {}

    private static final String SCHEMA_PATH = "/contracts/reading.schema.json";

    private final ObjectMapper mapper = new ObjectMapper();
    private final JsonSchema schema;

    public ReadingValidator() throws IOException {
        try (InputStream in = ReadingValidator.class.getResourceAsStream(SCHEMA_PATH)) {
            if (in == null) {
                throw new IllegalStateException("schema not on classpath: " + SCHEMA_PATH);
            }
            this.schema = JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V202012).getSchema(in);
        }
    }

    public Result validate(String payload) {
        if (payload == null || payload.isBlank()) {
            return new Invalid("empty payload");
        }
        JsonNode node;
        try {
            node = mapper.readTree(payload);
        } catch (JsonProcessingException e) {
            return new Invalid("not valid JSON");
        }
        Set<ValidationMessage> errors = schema.validate(node);
        if (!errors.isEmpty()) {
            return new Invalid("schema violation: "
                    + errors.stream().map(ValidationMessage::getMessage).sorted().collect(Collectors.joining("; ")));
        }
        Instant ts;
        try {
            ts = Instant.parse(node.get("timestamp").asText());
        } catch (DateTimeParseException e) {
            return new Invalid("timestamp is not a real date-time");
        }
        return new Valid(new RawReading(
                node.get("id").asText(),
                node.get("deviceId").asText(),
                node.get("metric").asText(),
                node.get("value").asDouble(),
                node.get("unit").asText(),
                ts));
    }
}
