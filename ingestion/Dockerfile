# Build context is the repository root (the shared contract lives in /contracts).
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /build
COPY contracts ./contracts
COPY ingestion/pom.xml ./ingestion/pom.xml
RUN mvn -q -B -f ingestion/pom.xml dependency:go-offline
COPY ingestion/src ./ingestion/src
RUN mvn -q -B -f ingestion/pom.xml package -DskipTests

FROM eclipse-temurin:25-jre
# pebble (Canonical's service manager) ships in the base image but is never run here; removing it
# drops a Go binary that vulnerability scans flag
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* /usr/bin/pebble \
    && useradd --system --uid 10001 app
WORKDIR /app
COPY --from=build /build/ingestion/target/ingestion.jar app.jar
USER 10001
EXPOSE 8080 8081
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75", "-jar", "app.jar"]
