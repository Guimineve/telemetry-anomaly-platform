FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
USER nobody
EXPOSE 8001
CMD ["telemetry-simulator"]
