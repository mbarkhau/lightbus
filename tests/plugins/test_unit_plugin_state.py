import asyncio
import pytest

from lightbus.api import Api, Event
from lightbus.path import BusPath
from lightbus.plugins.state import StatePlugin
from lightbus.utilities.async_tools import cancel

pytestmark = pytest.mark.unit


class TestApi(Api):
    my_event = Event(parameters=[])

    class Meta:
        name = "example.test"


@pytest.mark.asyncio
async def test_before_server_start(dummy_bus: BusPath, loop, get_dummy_events):
    await dummy_bus.client.register_api_async(TestApi())
    listener = await dummy_bus.example.test.my_event.listen_async(
        lambda *a, **kw: None, listener_name="test"
    )
    await asyncio.sleep(0.1)  # Give the bus a moment to kick up the listener

    state_plugin = StatePlugin(service_name="foo", process_name="bar")
    state_plugin.ping_enabled = False
    await state_plugin.before_server_start(client=dummy_bus.client)
    await cancel(listener)

    dummy_events = get_dummy_events()
    assert len(dummy_events) == 1
    event_message = dummy_events[0]

    assert event_message.api_name == "internal.state"
    assert event_message.event_name == "server_started"

    assert event_message.kwargs["api_names"] == ["example.test"]
    assert event_message.kwargs["listening_for"] == ["example.test.my_event"]
    assert event_message.kwargs["metrics_enabled"] == False
    assert event_message.kwargs["ping_enabled"] == False
    assert event_message.kwargs["ping_interval"] == 60
    assert event_message.kwargs["service_name"] == "foo"
    assert event_message.kwargs["process_name"] == "bar"


@pytest.mark.asyncio
async def test_ping(dummy_bus: BusPath, loop, get_dummy_events):
    # We check the pings message contains a list of registries, so register one
    await dummy_bus.client.register_api_async(TestApi())
    # Likewise for event listeners
    await dummy_bus.example.test.my_event.listen_async(lambda *a, **kw: None, listener_name="test")

    # Let the state plugin send a ping then cancel it
    state_plugin = StatePlugin(service_name="foo", process_name="bar")
    state_plugin.ping_interval = 0.1
    task = asyncio.ensure_future(state_plugin._send_ping(client=dummy_bus.client), loop=loop)
    await asyncio.sleep(0.15)
    await cancel(task)

    dummy_events = get_dummy_events()
    assert len(dummy_events) == 1
    event_message = dummy_events[0]

    assert event_message.api_name == "internal.state"
    assert event_message.event_name == "server_ping"

    assert event_message.kwargs["api_names"] == ["example.test"]
    assert event_message.kwargs["listening_for"] == ["example.test.my_event"]
    assert event_message.kwargs["metrics_enabled"] == False
    assert event_message.kwargs["ping_enabled"] == True
    assert event_message.kwargs["ping_interval"] == 0.1
    assert event_message.kwargs["service_name"] == "foo"
    assert event_message.kwargs["process_name"] == "bar"


@pytest.mark.asyncio
async def test_after_server_stopped(dummy_bus: BusPath, loop, get_dummy_events):
    await dummy_bus.client.register_api_async(TestApi())
    listener = await dummy_bus.example.test.my_event.listen_async(
        lambda *a, **kw: None, listener_name="test"
    )

    plugin = StatePlugin(service_name="foo", process_name="bar")
    plugin._ping_task = asyncio.Future()
    await plugin.after_server_stopped(client=dummy_bus.client)

    # Give any pending coroutines coroutines a moment to be awaited
    await asyncio.sleep(0.001)

    dummy_events = get_dummy_events()
    assert len(dummy_events) == 1
    event_message = dummy_events[0]

    assert event_message.api_name == "internal.state"
    assert event_message.event_name == "server_stopped"
    assert event_message.kwargs["service_name"] == "foo"
    assert event_message.kwargs["process_name"] == "bar"

    await cancel(listener)
