import pytest

import lightbus
import lightbus.path
from lightbus.path import BusPath
from lightbus.exceptions import InvalidBusPathConfiguration, InvalidParameters

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_init_root_with_name():
    with pytest.raises(InvalidBusPathConfiguration):
        BusPath(name="root", parent=None, client=None)


@pytest.mark.asyncio
async def test_ancestors():
    root_node = BusPath(name="", parent=None, client=None)
    child_node1 = BusPath(name="my_api", parent=root_node, client=None)
    child_node2 = BusPath(name="auth", parent=child_node1, client=None)
    assert list(child_node2.ancestors(include_self=True)) == [child_node2, child_node1, root_node]


@pytest.mark.asyncio
async def test_fully_qualified_name():
    root_node = BusPath(name="", parent=None, client=None)
    child_node1 = BusPath(name="my_api", parent=root_node, client=None)
    child_node2 = BusPath(name="auth", parent=child_node1, client=None)
    assert root_node.fully_qualified_name == ""
    assert child_node1.fully_qualified_name == "my_api"
    assert child_node1.fully_qualified_name == "my_api"
    assert str(child_node2) == "my_api.auth"


@pytest.mark.asyncio
async def test_dir(dummy_bus: lightbus.path.BusPath, dummy_api):
    await dummy_bus.client.register_api_async(dummy_api)
    assert "my" in dir(dummy_bus)
    assert "dummy" in dir(dummy_bus.my)
    assert "my_event" in dir(dummy_bus.my.dummy)
    assert "my_proc" in dir(dummy_bus.my.dummy)

    # Make sure we don't error if the api/rpc/event doesn't exist
    dir(dummy_bus.foo)
    dir(dummy_bus.foo.bar)


@pytest.mark.asyncio
async def test_positional_only_rpc(dummy_bus: lightbus.path.BusPath):
    with pytest.raises(InvalidParameters):
        await dummy_bus.my.dummy.my_proc.call_async(123)


@pytest.mark.asyncio
async def test_positional_only_event(dummy_bus: lightbus.path.BusPath):
    with pytest.raises(InvalidParameters):
        await dummy_bus.my.dummy.event.fire_async(123)
