import json

import pytest

from lightbus import DebugRpcTransport
from lightbus.config import Config
from lightbus.config.config import config_as_json_schema
from lightbus.plugins import PluginRegistry
from lightbus.utilities.casting import cast_to_hint
from lightbus.config.structure import RootConfig, BusConfig, LogLevelEnum
from lightbus.plugins.metrics import MetricsPlugin
from lightbus.plugins.state import StatePlugin
from lightbus.schema.encoder import json_encode
from lightbus.transports.redis import RedisRpcTransport

pytestmark = pytest.mark.unit


def test_config_as_json_schema():
    schema = config_as_json_schema()

    assert "$schema" in schema
    assert "bus" in schema["properties"]
    assert schema["additionalProperties"] == False
    assert schema["type"] == "object"


def test_config_as_json_schema_bus():
    schema = config_as_json_schema()
    assert schema["properties"]["bus"]


def test_config_as_json_schema_apis():
    schema = config_as_json_schema()
    assert schema["properties"]["apis"]
    assert ".*" in schema["properties"]["apis"]["patternProperties"]

    bus_config_schema = schema["properties"]["bus"]
    api_config_schema = schema["properties"]["apis"]["patternProperties"][".*"]["properties"]

    assert api_config_schema["event_transport"]["oneOf"][0]["type"] == "object"
    assert api_config_schema["rpc_transport"]["oneOf"][0]["type"] == "object"
    assert api_config_schema["result_transport"]["oneOf"][0]["type"] == "object"

    assert api_config_schema["rpc_timeout"]["type"] == "number"
    assert api_config_schema["event_listener_setup_timeout"]["type"] == "number"
    assert api_config_schema["event_fire_timeout"]["type"] == "number"
    assert api_config_schema["validate"]["oneOf"]

    assert (
        bus_config_schema["properties"]["schema"]["properties"]["transport"]["oneOf"][0]["type"]
        == "object"
    )


def test_config_as_json_schema_redis():
    schema = config_as_json_schema()

    bus_config_schema = schema["properties"]["bus"]
    api_config_schema = schema["properties"]["apis"]["patternProperties"][".*"]["properties"]

    redis_rpc_transport = api_config_schema["rpc_transport"]["oneOf"][0]["properties"]["redis"][
        "oneOf"
    ][0]
    redis_result_transport = api_config_schema["result_transport"]["oneOf"][0]["properties"][
        "redis"
    ]["oneOf"][0]
    redis_event_transport = api_config_schema["event_transport"]["oneOf"][0]["properties"]["redis"][
        "oneOf"
    ][0]

    assert redis_rpc_transport["type"] == "object"
    assert redis_result_transport["type"] == "object"
    assert redis_event_transport["type"] == "object"

    redis_schema_transport = bus_config_schema["properties"]["schema"]["properties"]["transport"][
        "oneOf"
    ][0]["properties"]["redis"]["oneOf"][0]
    assert redis_schema_transport["type"] == "object"


def test_config_as_json_schema_dump():
    """Make sure we can encode the schema as json

    We may have some custom types kicking around (i.e. frozendict())
    """
    schema = config_as_json_schema()
    encoded = json_encode(schema)
    assert encoded
    json.loads(encoded)


def test_default_config():
    config = Config.load_dict({})
    assert config.bus()
    assert config.api()
    assert config.api().rpc_transport.redis
    assert config.api().result_transport.redis
    assert config.api().event_transport.redis
    assert config.bus().schema.transport.redis


def test_load_bus_config(tmp_file):
    tmp_file.write("bus: { log_level: warning }")
    tmp_file.flush()
    config = Config.load_file(tmp_file.name)
    assert config.bus().log_level == LogLevelEnum.WARNING


def test_cast_to_hint_ok():
    root_config = cast_to_hint({"bus": {"log_level": "warning"}}, RootConfig)
    assert root_config.bus.log_level == LogLevelEnum.WARNING


def test_cast_to_hint_apis():
    root_config = cast_to_hint({"apis": {"my_api": {"rpc_timeout": 1}}}, RootConfig)
    assert root_config.apis["my_api"].rpc_timeout == 1


def test_cast_to_hint_unknown_property():
    root_config = cast_to_hint({"bus": {"foo": "xyz"}}, RootConfig)
    assert not hasattr(root_config.bus, "foo")


def test_api_config_default():
    config = Config.load_yaml(EXAMPLE_VALID_YAML)
    assert config.api("foo").event_transport.redis.batch_size == 50


def test_api_config_customised():
    config = Config.load_yaml(EXAMPLE_VALID_YAML)
    assert config.api("my.api").event_transport.redis.batch_size == 1


def test_cast_to_hint_validate():
    root_config = cast_to_hint(
        {"apis": {"my_api": {"validate": {"incoming": True, "outgoing": False}}}}, RootConfig
    )
    assert root_config.apis["my_api"].validate.incoming == True
    assert root_config.apis["my_api"].validate.outgoing == False


def test_plugin_selector_config():
    config = Config.load_dict({})
    assert hasattr(config._config.plugins, "internal_state")
    assert hasattr(config._config.plugins, "internal_metrics")
    assert config.plugin("internal_state").ping_enabled is True
    assert config.plugin("internal_state").ping_interval > 0
    assert config.plugin("internal_state").enabled is True


def test_plugin_selector_custom_config():
    config = Config.load_dict({"plugins": {"internal_state": {"ping_interval": 123}}})
    assert config.plugin("internal_state").ping_interval == 123


def test_plugin_disabled(plugin_registry: PluginRegistry):
    config = Config.load_dict(
        {"plugins": {"internal_state": {"enabled": False}, "internal_metrics": {"enabled": False}}}
    )
    plugin_registry.autoload_plugins(config)
    assert not plugin_registry._plugins


def test_plugin_enabled(plugin_registry: PluginRegistry):
    config = Config.load_dict(
        {"plugins": {"internal_state": {"enabled": True}, "internal_metrics": {"enabled": True}}}
    )
    plugin_registry.autoload_plugins(config)
    assert plugin_registry._plugins


EXAMPLE_VALID_YAML = """
bus:
    log_level: warning

apis:
    default:
        event_transport:
            redis:
                batch_size: 50
                
    my.api:
        event_transport:
            redis:
                batch_size: 1
"""
