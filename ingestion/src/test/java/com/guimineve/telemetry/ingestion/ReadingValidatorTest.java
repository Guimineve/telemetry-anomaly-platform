package com.guimineve.telemetry.ingestion;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.IOException;
import java.time.Instant;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;

class ReadingValidatorTest {

    private final ReadingValidator validator = new ReadingValidator();

    ReadingValidatorTest() throws IOException {}

    static String reading(String overrides) {
        String base = """
                "id":"3f2b8c1e-5a6d-4e7f-9a10-b2c3d4e5f607","deviceId":"sensor-001",\
                "metric":"temperature","value":41.2,"unit":"celsius",\
                "timestamp":"2026-01-01T10:00:00.123Z\"""";
        return "{" + base + overrides + "}";
    }

    @Test
    void acceptsAWellFormedReading() {
        var result = validator.validate(reading(""));
        assertThat(result).isInstanceOfSatisfying(ReadingValidator.Valid.class, v -> {
            assertThat(v.reading().deviceId()).isEqualTo("sensor-001");
            assertThat(v.reading().value()).isEqualTo(41.2);
            assertThat(v.reading().timestamp()).isEqualTo(Instant.parse("2026-01-01T10:00:00.123Z"));
        });
    }

    @Test
    void acceptsAnIntegerValue() {
        String json = reading("").replace("41.2", "7");
        assertThat(validator.validate(json)).isInstanceOf(ReadingValidator.Valid.class);
    }

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {"   "})
    void rejectsEmptyPayloads(String payload) {
        assertThat(validator.validate(payload))
                .isEqualTo(new ReadingValidator.Invalid("empty payload"));
    }

    @Test
    void rejectsNonJson() {
        assertThat(validator.validate("not json at all {"))
                .isEqualTo(new ReadingValidator.Invalid("not valid JSON"));
    }

    @Test
    void rejectsMissingRequiredField() {
        String json = reading("").replace("\"value\":41.2,", "");
        assertInvalid(json, "value");
    }

    @Test
    void rejectsWrongType() {
        assertInvalid(reading("").replace("41.2", "\"hot\""), "value");
    }

    @Test
    void rejectsUnknownMetric() {
        assertInvalid(reading("").replace("temperature", "humidity"), "metric");
    }

    @Test
    void rejectsAdditionalProperties() {
        assertInvalid(reading(",\"extra\":1"), "extra");
    }

    @Test
    void rejectsBadUuid() {
        assertInvalid(reading("").replace("3f2b8c1e-5a6d-4e7f-9a10-b2c3d4e5f607", "nope"), "id");
    }

    @Test
    void rejectsBadDeviceId() {
        assertInvalid(reading("").replace("sensor-001", "Bad Device!"), "deviceId");
    }

    @Test
    void rejectsImpossibleDate() {
        String json = reading("").replace("2026-01-01T10:00:00.123Z", "2026-13-45T10:00:00Z");
        assertThat(validator.validate(json))
                .isEqualTo(new ReadingValidator.Invalid("timestamp is not a real date-time"));
    }

    private void assertInvalid(String json, String mentions) {
        assertThat(validator.validate(json)).isInstanceOfSatisfying(ReadingValidator.Invalid.class, i -> {
            assertThat(i.reason()).startsWith("schema violation");
            assertThat(i.reason()).contains(mentions);
        });
    }
}
