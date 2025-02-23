import asyncio
import logging
from datetime import datetime

import pytest

from lightbus.config import Config
from lightbus.message import EventMessage
from lightbus.serializers import (
    ByFieldMessageSerializer,
    ByFieldMessageDeserializer,
    BlobMessageSerializer,
    BlobMessageDeserializer,
)
from lightbus.transports.redis import RedisEventTransport, StreamUse, RedisEventMessage
from lightbus.utilities.async_tools import cancel

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_connection_manager(redis_event_transport):
    """Does get_redis() provide a working redis connection"""
    connection_manager = await redis_event_transport.connection_manager()
    with await connection_manager as redis:
        assert await redis.info()


@pytest.mark.asyncio
async def test_send_event(redis_event_transport: RedisEventTransport, redis_client):
    await redis_event_transport.send_event(
        EventMessage(api_name="my.api", event_name="my_event", id="123", kwargs={"field": "value"}),
        options={},
        bus_client=None,
    )
    messages = await redis_client.xrange("my.api.my_event:stream")
    assert len(messages) == 1
    assert messages[0][1] == {
        b"api_name": b"my.api",
        b"event_name": b"my_event",
        b"id": b"123",
        b"version": b"1",
        b":field": b'"value"',
    }


@pytest.mark.asyncio
async def test_send_event_per_api_stream(redis_event_transport: RedisEventTransport, redis_client):
    redis_event_transport.stream_use = StreamUse.PER_API
    await redis_event_transport.send_event(
        EventMessage(api_name="my.api", event_name="my_event", kwargs={"field": "value"}, id="123"),
        options={},
        bus_client=None,
    )
    messages = await redis_client.xrange("my.api.*:stream")
    assert len(messages) == 1
    assert messages[0][1] == {
        b"api_name": b"my.api",
        b"event_name": b"my_event",
        b"id": b"123",
        b"version": b"1",
        b":field": b'"value"',
    }


@pytest.mark.asyncio
async def test_consume_events(
    loop, redis_event_transport: RedisEventTransport, redis_client, dummy_api
):
    async def co_enqeue():
        await asyncio.sleep(0.1)
        return await redis_client.xadd(
            "my.dummy.my_event:stream",
            fields={
                b"api_name": b"my.dummy",
                b"event_name": b"my_event",
                b"id": b"123",
                b"version": b"1",
                b":field": b'"value"',
            },
        )

    async def co_consume():
        async for message_ in redis_event_transport.consume(
            [("my.dummy", "my_event")], "test_listener", bus_client=None
        ):
            return message_

    enqueue_result, messages = await asyncio.gather(co_enqeue(), co_consume())
    message = messages[0]
    assert message.api_name == "my.dummy"
    assert message.event_name == "my_event"
    assert message.kwargs == {"field": "value"}
    assert message.native_id
    assert type(message.native_id) == str


@pytest.mark.asyncio
async def test_consume_events_multiple_consumers(loop, redis_pool, redis_client, dummy_api):
    messages = []

    async def co_consume(group_number):
        event_transport = RedisEventTransport(
            redis_pool=redis_pool,
            service_name=f"test_service{group_number}",
            consumer_name=f"test_consumer",
            stream_use=StreamUse.PER_EVENT,
        )

        async for messages_ in event_transport.consume(
            [("my.dummy", "my_event")], "test_listener", bus_client=None
        ):
            messages.append(messages_)
            await event_transport.acknowledge(*messages_, bus_client=None)

    task1 = asyncio.ensure_future(co_consume(1))
    task2 = asyncio.ensure_future(co_consume(2))

    await asyncio.sleep(0.1)
    await redis_client.xadd(
        "my.dummy.my_event:stream",
        fields={
            b"api_name": b"my.dummy",
            b"event_name": b"my_event",
            b"id": b"123",
            b"version": b"1",
            b":field": b'"value"',
        },
    )
    await asyncio.sleep(0.1)
    await cancel(task1, task2)

    assert len(messages) == 2


@pytest.mark.asyncio
async def test_consume_events_multiple_consumers_one_group(
    loop, redis_pool, redis_client, dummy_api
):
    events = []

    async def co_consume(consumer_number):
        event_transport = RedisEventTransport(
            redis_pool=redis_pool,
            service_name="test_service",
            consumer_name=f"test_consumer{consumer_number}",
            stream_use=StreamUse.PER_EVENT,
        )
        consumer = event_transport.consume(
            listen_for=[("my.dummy", "my_event")], listener_name="test_listener", bus_client=None
        )
        async for messages in consumer:
            events.append(messages)
            await event_transport.acknowledge(*messages, bus_client=None)

    task1 = asyncio.ensure_future(co_consume(1))
    task2 = asyncio.ensure_future(co_consume(2))
    await asyncio.sleep(0.1)

    await redis_client.xadd(
        "my.dummy.my_event:stream",
        fields={
            b"api_name": b"my.dummy",
            b"event_name": b"my_event",
            b"id": b"123",
            b"version": b"1",
            b":field": b'"value"',
        },
    )
    await asyncio.sleep(0.1)
    await cancel(task1, task2)

    assert len(events) == 1


@pytest.mark.asyncio
async def test_consume_events_since_id(
    loop, redis_event_transport: RedisEventTransport, redis_client, dummy_api
):
    await redis_client.xadd(
        "my.dummy.my_event:stream",
        fields={
            b"api_name": b"my.dummy",
            b"event_name": b"my_event",
            b"id": b"123",
            b"version": b"1",
            b":field": b'"1"',
        },
        message_id="1515000001000-0",
    )
    await redis_client.xadd(
        "my.dummy.my_event:stream",
        fields={
            b"api_name": b"my.dummy",
            b"event_name": b"my_event",
            b"id": b"123",
            b"version": b"1",
            b":field": b'"2"',
        },
        message_id="1515000002000-0",
    )
    await redis_client.xadd(
        "my.dummy.my_event:stream",
        fields={
            b"api_name": b"my.dummy",
            b"event_name": b"my_event",
            b"id": b"123",
            b"version": b"1",
            b":field": b'"3"',
        },
        message_id="1515000003000-0",
    )

    consumer = redis_event_transport.consume(
        [("my.dummy", "my_event")], "cg", since="1515000001500-0", forever=False, bus_client=None
    )

    events = []

    async def co():
        async for messages in consumer:
            events.extend(messages)
            await redis_event_transport.acknowledge(*messages, bus_client=None)

    task = asyncio.ensure_future(co())
    await asyncio.sleep(0.1)
    await cancel(task)

    messages_ids = [m.native_id for m in events if isinstance(m, EventMessage)]
    assert len(messages_ids) == 2
    assert len(events) == 2
    assert events[0].kwargs["field"] == "2"
    assert events[1].kwargs["field"] == "3"


@pytest.mark.asyncio
async def test_consume_events_since_datetime(
    loop, redis_event_transport: RedisEventTransport, redis_client, dummy_api
):
    await redis_client.xadd(
        "my.dummy.my_event:stream",
        fields={
            b"api_name": b"my.dummy",
            b"event_name": b"my_event",
            b"id": b"123",
            b"version": b"1",
            b":field": b'"1"',
        },
        message_id="1515000001000-0",
    )
    await redis_client.xadd(
        "my.dummy.my_event:stream",
        fields={
            b"api_name": b"my.dummy",
            b"event_name": b"my_event",
            b"id": b"123",
            b"version": b"1",
            b":field": b'"2"',
        },
        message_id="1515000002000-0",
    )
    await redis_client.xadd(
        "my.dummy.my_event:stream",
        fields={
            b"api_name": b"my.dummy",
            b"event_name": b"my_event",
            b"id": b"123",
            b"version": b"1",
            b":field": b'"3"',
        },
        message_id="1515000003000-0",
    )

    # 1515000001500-0 -> 2018-01-03T17:20:01.500Z
    since_datetime = datetime(2018, 1, 3, 17, 20, 1, 500)
    consumer = redis_event_transport.consume(
        [("my.dummy", "my_event")], {}, since=since_datetime, forever=False, bus_client=None
    )

    events = []

    async def co():
        async for messages in consumer:
            events.extend(messages)
            await redis_event_transport.acknowledge(*messages, bus_client=None)

    task = asyncio.ensure_future(co())
    await asyncio.sleep(0.1)
    await cancel(task)

    assert len(events) == 2
    assert events[0].kwargs["field"] == "2"
    assert events[1].kwargs["field"] == "3"


@pytest.mark.asyncio
async def test_from_config(redis_client):
    await redis_client.select(5)
    host, port = redis_client.address
    transport = RedisEventTransport.from_config(
        config=Config.load_dict({}),
        url=f"redis://127.0.0.1:{port}/5",
        connection_parameters=dict(maxsize=123),
        batch_size=123,
        # Non default serializers, event though they wouldn't make sense in this context
        serializer="lightbus.serializers.BlobMessageSerializer",
        deserializer="lightbus.serializers.BlobMessageDeserializer",
    )
    with await transport.connection_manager() as transport_client:
        assert transport_client.connection.address == ("127.0.0.1", port)
        assert transport_client.connection.db == 5
        await transport_client.set("x", 1)
        assert await redis_client.get("x")

    assert transport._redis_pool.connection.maxsize == 123
    assert isinstance(transport.serializer, BlobMessageSerializer)
    assert isinstance(transport.deserializer, BlobMessageDeserializer)


@pytest.mark.asyncio
async def test_reclaim_lost_messages(loop, redis_client, redis_pool, dummy_api):
    """Test that messages which another consumer has timed out on can be reclaimed"""

    # Add a message
    await redis_client.xadd(
        "my.dummy.my_event:stream",
        fields={
            b"api_name": b"my.dummy",
            b"event_name": b"my_event",
            b"id": b"123",
            b"version": b"1",
            b":field": b'"value"',
        },
    )
    # Create the consumer group
    await redis_client.xgroup_create(
        stream="my.dummy.my_event:stream", group_name="test_service", latest_id="0"
    )

    # Claim it in the name of another consumer
    result = await redis_client.xread_group(
        group_name="test_service",
        consumer_name="bad_consumer",
        streams=["my.dummy.my_event:stream"],
        latest_ids=[">"],
    )
    assert result, "Didn't actually manage to claim any message"

    # Sleep a moment to fake a short timeout
    await asyncio.sleep(0.1)

    event_transport = RedisEventTransport(
        redis_pool=redis_pool,
        service_name="test_service",
        consumer_name="good_consumer",
        acknowledgement_timeout=0.01,  # in ms, short for the sake of testing
        stream_use=StreamUse.PER_EVENT,
    )
    reclaimer = event_transport._reclaim_lost_messages(
        stream_names=["my.dummy.my_event:stream"],
        consumer_group="test_service",
        expected_events={"my_event"},
    )

    reclaimed_messages = []
    async for m in reclaimer:
        reclaimed_messages.extend(m)

    assert len(reclaimed_messages) == 1
    assert reclaimed_messages[0].native_id
    assert type(reclaimed_messages[0].native_id) == str


@pytest.mark.asyncio
async def test_reclaim_lost_messages_ignores_non_timed_out_messages(
    loop, redis_client, redis_pool, dummy_api
):
    """Ensure messages which have not timed out are not reclaimed"""

    # Add a message
    await redis_client.xadd(
        "my.dummy.my_event:stream",
        fields={
            b"api_name": b"my.dummy",
            b"event_name": b"my_event",
            b"id": b"123",
            b":field": b'"value"',
        },
    )
    # Create the consumer group
    await redis_client.xgroup_create(
        stream="my.dummy.my_event:stream", group_name="test_service", latest_id="0"
    )

    # Claim it in the name of another consumer
    await redis_client.xread_group(
        group_name="test_service",
        consumer_name="bad_consumer",
        streams=["my.dummy.my_event:stream"],
        latest_ids=[">"],
    )
    # Sleep a moment to fake a short timeout
    await asyncio.sleep(0.1)

    event_transport = RedisEventTransport(
        redis_pool=redis_pool,
        service_name="test_service",
        consumer_name="good_consumer",
        # in ms, longer as we want to check that the messages is not reclaimed
        acknowledgement_timeout=0.9,
        stream_use=StreamUse.PER_EVENT,
    )
    reclaimer = event_transport._reclaim_lost_messages(
        stream_names=["my.dummy.my_event:stream"],
        consumer_group="test_service",
        expected_events={"my_event"},
    )
    reclaimed_messages = [m async for m in reclaimer]
    assert len(reclaimed_messages) == 0


@pytest.mark.asyncio
async def test_reclaim_lost_messages_consume(loop, redis_client, redis_pool, dummy_api):
    """Test that messages which another consumer has timed out on can be reclaimed

    Unlike the above test, we call consume() here, not _reclaim_lost_messages()
    """

    # Add a message
    await redis_client.xadd(
        "my.dummy.my_event:stream",
        fields={
            b"api_name": b"my.dummy",
            b"event_name": b"my_event",
            b"id": b"123",
            b"version": b"1",
            b":field": b'"value"',
        },
    )
    # Create the consumer group
    await redis_client.xgroup_create(
        stream="my.dummy.my_event:stream", group_name="test_service-test_listener", latest_id="0"
    )

    # Claim it in the name of another consumer
    await redis_client.xread_group(
        group_name="test_service-test_listener",
        consumer_name="bad_consumer",
        streams=["my.dummy.my_event:stream"],
        latest_ids=[">"],
    )
    # Sleep a moment to fake a short timeout
    await asyncio.sleep(0.1)

    event_transport = RedisEventTransport(
        redis_pool=redis_pool,
        service_name="test_service",
        consumer_name="good_consumer",
        acknowledgement_timeout=0.01,  # in ms, short for the sake of testing
        stream_use=StreamUse.PER_EVENT,
    )
    consumer = event_transport.consume(
        listen_for=[("my.dummy", "my_event")],
        since="0",
        listener_name="test_listener",
        bus_client=None,
    )

    messages = []

    async def consume():
        async for messages_ in consumer:
            messages.extend(messages_)

    task = asyncio.ensure_future(consume())
    await asyncio.sleep(0.1)
    await cancel(task)

    assert len(messages) == 1


@pytest.mark.asyncio
async def test_reclaim_pending_messages(loop, redis_client, redis_pool, dummy_api):
    """Test that unacked messages belonging to this consumer get reclaimed on startup
    """

    # Add a message
    await redis_client.xadd(
        "my.dummy.my_event:stream",
        fields={
            b"api_name": b"my.dummy",
            b"event_name": b"my_event",
            b"id": b"123",
            b"version": b"1",
            b":field": b'"value"',
        },
    )
    # Create the consumer group
    await redis_client.xgroup_create(
        stream="my.dummy.my_event:stream", group_name="test_service-test_listener", latest_id="0"
    )

    # Claim it in the name of ourselves
    await redis_client.xread_group(
        group_name="test_service-test_listener",
        consumer_name="good_consumer",
        streams=["my.dummy.my_event:stream"],
        latest_ids=[">"],
    )

    event_transport = RedisEventTransport(
        redis_pool=redis_pool,
        service_name="test_service",
        consumer_name="good_consumer",
        stream_use=StreamUse.PER_EVENT,
    )
    consumer = event_transport.consume(
        listen_for=[("my.dummy", "my_event")],
        since="0",
        listener_name="test_listener",
        bus_client=None,
    )

    messages = []

    async def consume():
        async for messages_ in consumer:
            messages.extend(messages_)
            await event_transport.acknowledge(*messages_, bus_client=None)

    task = asyncio.ensure_future(consume())
    await asyncio.sleep(0.1)
    await cancel(task)

    assert len(messages) == 1
    assert messages[0].api_name == "my.dummy"
    assert messages[0].event_name == "my_event"
    assert messages[0].kwargs == {"field": "value"}
    assert messages[0].native_id
    assert type(messages[0].native_id) == str

    # Now check that redis believes the message has been consumed
    total_pending, *_ = await redis_client.xpending(
        stream="my.dummy.my_event:stream", group_name="test_service-test_listener"
    )
    assert total_pending == 0


@pytest.mark.asyncio
async def test_consume_events_create_consumer_group_first(
    loop, redis_client, redis_event_transport, dummy_api
):
    """Create the consumer group before the stream exists

    This should create a noop message which gets ignored by the event transport
    """
    consumer = redis_event_transport.consume(
        listen_for=[("my.dummy", "my_event")],
        since="0",
        listener_name="test_listener",
        bus_client=None,
    )
    messages = []

    async def consume():
        async for messages in consumer:
            messages.extend(messages)

    task = asyncio.ensure_future(consume())
    await asyncio.sleep(0.1)
    await cancel(task)
    assert len(messages) == 0


@pytest.mark.asyncio
async def test_max_len_truncating(redis_event_transport: RedisEventTransport, redis_client, caplog):
    """Make sure the event stream gets truncated

    Note that truncation is approximate
    """
    caplog.set_level(logging.WARNING)
    redis_event_transport.max_stream_length = 100
    for x in range(0, 200):
        await redis_event_transport.send_event(
            EventMessage(api_name="my.api", event_name="my_event", kwargs={"field": "value"}),
            options={},
            bus_client=None,
        )
    messages = await redis_client.xrange("my.api.my_event:stream")
    assert len(messages) >= 100
    assert len(messages) < 150


@pytest.mark.asyncio
async def test_max_len_set_to_none(
    redis_event_transport: RedisEventTransport, redis_client, caplog
):
    """Make sure the event stream does not get truncated when
    max_stream_length = None
    """
    caplog.set_level(logging.WARNING)
    redis_event_transport.max_stream_length = None
    for x in range(0, 200):
        await redis_event_transport.send_event(
            EventMessage(api_name="my.api", event_name="my_event", kwargs={"field": "value"}),
            options={},
            bus_client=None,
        )
    messages = await redis_client.xrange("my.api.my_event:stream")
    assert len(messages) == 200


@pytest.mark.asyncio
async def test_consume_events_per_api_stream(
    loop, redis_event_transport: RedisEventTransport, redis_client, dummy_api
):
    redis_event_transport.stream_use = StreamUse.PER_API

    events = []

    async def co_consume(event_name):
        consumer = redis_event_transport.consume([("my.dummy", event_name)], "cg", bus_client=None)
        async for messages in consumer:
            events.extend(messages)

    task1 = asyncio.ensure_future(co_consume("my_event1"))
    task2 = asyncio.ensure_future(co_consume("my_event2"))
    task3 = asyncio.ensure_future(co_consume("my_event3"))
    await asyncio.sleep(0.2)

    await redis_client.xadd(
        "my.dummy.*:stream",
        fields={
            b"api_name": b"my.dummy",
            b"event_name": b"my_event1",
            b"id": b"1",
            b"version": b"1",
            b":field": b'"value"',
        },
    )
    await redis_client.xadd(
        "my.dummy.*:stream",
        fields={
            b"api_name": b"my.dummy",
            b"event_name": b"my_event2",
            b"id": b"2",
            b"version": b"1",
            b":field": b'"value"',
        },
    )
    await redis_client.xadd(
        "my.dummy.*:stream",
        fields={
            b"api_name": b"my.dummy",
            b"event_name": b"my_event3",
            b"id": b"3",
            b"version": b"1",
            b":field": b'"value"',
        },
    )
    await asyncio.sleep(0.2)
    await cancel(task1, task2, task3)

    assert set([e.event_name for e in events]) == {"my_event1", "my_event2", "my_event3"}


@pytest.mark.asyncio
async def test_reconnect_upon_send_event(
    redis_event_transport: RedisEventTransport, redis_client, get_total_redis_connections
):
    await redis_client.execute(b"CLIENT", b"KILL", b"TYPE", b"NORMAL")
    assert await get_total_redis_connections() == 1

    await redis_event_transport.send_event(
        EventMessage(api_name="my.api", event_name="my_event", id="123", kwargs={"field": "value"}),
        options={},
        bus_client=None,
    )
    messages = await redis_client.xrange("my.api.my_event:stream")
    assert len(messages) == 1
    assert await get_total_redis_connections() == 2


@pytest.mark.asyncio
async def test_reconnect_while_listening(
    loop, redis_event_transport: RedisEventTransport, redis_client, dummy_api
):
    redis_event_transport.consumption_restart_delay = 0.0001

    async def co_enqeue():
        while True:
            await asyncio.sleep(0.1)
            logging.info("test_reconnect_while_listening: Sending message")
            await redis_client.xadd(
                "my.dummy.my_event:stream",
                fields={
                    b"api_name": b"my.dummy",
                    b"event_name": b"my_event",
                    b"id": b"123",
                    b"version": b"1",
                    b":field": b'"value"',
                },
            )

    total_messages = 0

    async def co_consume():
        nonlocal total_messages

        consumer = redis_event_transport.consume(
            [("my.dummy", "my_event")], "test_listener", bus_client=None
        )
        async for messages_ in consumer:
            total_messages += len(messages_)
            await redis_event_transport.acknowledge(*messages_, bus_client=None)

    enque_task = asyncio.ensure_future(co_enqeue())
    consume_task = asyncio.ensure_future(co_consume())

    await asyncio.sleep(0.2)
    assert total_messages > 0
    await redis_client.execute(b"CLIENT", b"KILL", b"TYPE", b"NORMAL")
    total_messages = 0
    await asyncio.sleep(0.2)
    assert total_messages > 0

    await cancel(enque_task, consume_task)


@pytest.mark.asyncio
async def test_acknowledge(redis_event_transport: RedisEventTransport, redis_client, dummy_api):
    message_id = await redis_client.xadd("test_api.test_event:stream", fields={"a": 1})
    await redis_client.xgroup_create("test_api.test_event:stream", "test_group", latest_id="0")

    messages = await redis_client.xread_group(
        "test_group", "test_consumer", ["test_api.test_event:stream"], latest_ids=[">"]
    )
    assert len(messages) == 1

    total_pending, *_ = await redis_client.xpending("test_api.test_event:stream", "test_group")
    assert total_pending == 1

    await redis_event_transport.acknowledge(
        RedisEventMessage(
            api_name="test_api",
            event_name="test_event",
            consumer_group="test_group",
            stream="test_api.test_event:stream",
            native_id=message_id,
        ),
        bus_client=None,
    )

    total_pending, *_ = await redis_client.xpending("test_api.test_event:stream", "test_group")
    assert total_pending == 0
