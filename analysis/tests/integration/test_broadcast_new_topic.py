"""The WebSocket broadcaster must pick up a topic that did not exist when it subscribed.

Regression for a startup race: the consumer joined its group before the alerts topic's partitions
were in the cluster metadata, was assigned nothing, and stayed silent until aiokafka's next metadata
refresh (5 minutes by default). With broker auto-creation on, the subscription itself may create
the topic, so this test does not always reproduce the race; it guards the behaviour either way.
"""

import asyncio
import time
import uuid

import pytest
from aiokafka import AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from analysis.broadcast import Broadcaster

pytestmark = pytest.mark.integration


class Client:
    def __init__(self) -> None:
        self.received: list[str] = []

    async def send_text(self, data: str) -> None:
        self.received.append(data)


async def test_broadcaster_receives_from_topic_created_after_subscribing(kafka_bootstrap):
    topic = f"alerts-{uuid.uuid4().hex[:8]}"
    broadcaster = Broadcaster()
    client = Client()
    broadcaster.add(client)
    await broadcaster.start(kafka_bootstrap, topic)
    producer = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap)
    admin = AIOKafkaAdminClient(bootstrap_servers=kafka_bootstrap)
    await producer.start()
    await admin.start()
    try:
        await asyncio.sleep(2)  # let the consumer join its group first
        try:
            await admin.create_topics([NewTopic(topic, 3, 1)])
        except TopicAlreadyExistsError:
            pass
        # keep producing: the consumer starts at "latest", so only messages after it has its
        # partitions count; well under aiokafka's 5-minute default refresh
        deadline = time.monotonic() + 30
        while not client.received and time.monotonic() < deadline:
            await producer.send_and_wait(topic, b'{"id":"a1"}', key=str(uuid.uuid4()).encode())
            await asyncio.sleep(1)
        assert client.received, "broadcaster never received from the new topic"
    finally:
        await broadcaster.stop()
        await producer.stop()
        await admin.close()
