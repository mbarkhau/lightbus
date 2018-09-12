import logging
from itertools import chain
from typing import (
    Sequence,
    Tuple,
    List,
    Generator,
    Dict,
    NamedTuple,
    TypeVar,
    Type,
    Set,
    AsyncGenerator,
)
import inspect

from lightbus.api import Api
from lightbus.exceptions import NothingToListenFor, TransportNotFound
from lightbus.message import RpcMessage, EventMessage, ResultMessage
from lightbus.utilities.config import make_from_config_structure, random_name
from lightbus.utilities.importing import load_entrypoint_classes

T = TypeVar("T")
logger = logging.getLogger(__name__)


class TransportMetaclass(type):

    def __new__(mcs, name, bases, attrs, **kwds):
        cls = super().__new__(mcs, name, bases, attrs)
        if not hasattr(cls, f"{name}Config") and hasattr(cls, "from_config"):
            cls.Config = make_from_config_structure(
                class_name=name, from_config_method=cls.from_config
            )
        return cls


class Transport(object, metaclass=TransportMetaclass):

    @classmethod
    def from_config(cls: Type[T], *config) -> T:
        return cls()

    async def open(self):
        """Setup transport prior to use


        Can be used for opening connections, initialisation, etc.
        """
        pass

    async def close(self):
        """Cleanup prior to termination

        Can be used for closing connections etc.
        """
        pass


class RpcTransport(Transport):
    """Implement the sending and receiving of RPC calls"""

    async def call_rpc(self, rpc_message: RpcMessage, options: dict):
        """Publish a call to a remote procedure"""
        RpcTransport.from_config()
        raise NotImplementedError()

    async def consume_rpcs(self, apis: Sequence[Api]) -> Sequence[RpcMessage]:
        """Consume RPC calls for the given API"""
        raise NotImplementedError()


class ResultTransport(Transport):
    """Implement the send & receiving of results

    """

    def get_return_path(self, rpc_message: RpcMessage) -> str:
        raise NotImplementedError()

    async def send_result(
        self, rpc_message: RpcMessage, result_message: ResultMessage, return_path: str
    ):
        """Send a result back to the caller

        Args:
            rpc_message (): The original message received from the client
            result_message (): The result message to be sent back to the client
            return_path (str): The string indicating where to send the result.
                As generated by :ref:`get_return_path()`.
        """
        raise NotImplementedError()

    async def receive_result(
        self, rpc_message: RpcMessage, return_path: str, options: dict
    ) -> ResultMessage:
        """Receive the result for the given message

        Args:
            rpc_message (): The original message sent to the server
            return_path (str): The string indicated where to receive the result.
                As generated by :ref:`get_return_path()`.
            options (dict): Dictionary of options specific to this particular backend
        """
        raise NotImplementedError()


class EventTransport(Transport):
    """ Implement the sending/consumption of events over a given transport.
    """

    async def send_event(self, event_message: EventMessage, options: dict):
        """Publish an event"""
        raise NotImplementedError()

    async def consume(
        self, listen_for: List[Tuple[str, str]], listener_name: str = None, **kwargs
    ) -> AsyncGenerator[List[EventMessage], None]:
        """Consume messages for the given APIs

        Examples:

            Consuming events::

                listen_for = [
                    ('mycompany.auth', 'user_created'),
                    ('mycompany.auth', 'user_updated'),
                ]
                async with event_transport.consume(listen_for) as event_message:
                    print(event_message)

        """
        raise NotImplementedError(
            f"Event transport {self.__class__.__name__} does not support listening for events"
        )

    async def acknowledge(self, *event_messages):
        """Acknowledge that one or more events were successfully processed"""
        pass

    async def history(self, listen_for: List[Tuple[str, str]]):
        raise NotImplementedError(
            f"Event transport {self.__class__.__name__} does not support fetching past events"
        )

    def _sanity_check_listen_for(self, listen_for):
        """Utility method to sanity check the `listen_for` parameter.

        Call at the start of your consume() implementation.
        """
        if not listen_for:
            raise NothingToListenFor(
                "EventTransport.consume() was called without providing anything "
                'to listen for in the "listen_for" argument.'
            )


class SchemaTransport(Transport):
    """ Implement sharing of lightbus API schemas
    """

    async def store(self, api_name: str, schema: Dict, ttl_seconds: int):
        """Store a schema for the given API"""
        raise NotImplementedError()

    async def ping(self, api_name: str, schema: Dict, ttl_seconds: int):
        """Keep alive a schema already stored via store()

        The defaults to simply calling store() on the assumption that this
        will cause the ttl to be updated. Backends may choose to
        customise this logic.
        """
        await self.store(api_name, schema, ttl_seconds)

    async def load(self) -> Dict[str, Dict]:
        """Load the schema for all APIs

        Should return a mapping of API names to schemas
        """
        raise NotImplementedError()


empty = NamedTuple("Empty")


class TransportRegistry(object):
    """ Manages access to transports

    It is possible for different APIs within lightbus to use different transports.
    This registry handles the logic of loading the transports for a given
    configuration. Thereafter, it provides access to these transports based on
    a given API.

    The 'default' API is a special case as it is fallback transport for
    any APIs that do not have their own specific transports configured.
    """

    class _RegistryEntry(NamedTuple):
        rpc: RpcTransport = None
        result: ResultTransport = None
        event: EventTransport = None

    schema_transport: SchemaTransport = None

    def __init__(self):
        self._registry: Dict[str, TransportRegistry._RegistryEntry] = {}

    def load_config(self, config: "Config") -> "TransportRegistry":
        for api_name, api_config in config.apis().items():
            for transport_type in ("event", "rpc", "result"):
                transport_selector = getattr(api_config, f"{transport_type}_transport")
                transport_config = self._get_transport_config(transport_selector)
                if transport_config:
                    transport_name, transport_config = transport_config
                    transport = self._instantiate_transport(
                        transport_type, transport_name, transport_config, config
                    )
                    self._set_transport(api_name, transport, transport_type)

        # Schema transport
        transport_config = self._get_transport_config(config.bus().schema.transport)
        if transport_config:
            transport_name, transport_config = transport_config
            self.schema_transport = self._instantiate_transport(
                "schema", transport_name, transport_config, config
            )

        return self

    def _get_transport_config(self, transport_selector):
        if transport_selector:
            for transport_name in transport_selector._fields:
                transport_config = getattr(transport_selector, transport_name)
                if transport_config is not None:
                    return transport_name, transport_config

    def _instantiate_transport(self, type_, name, transport_config: NamedTuple, config: "Config"):
        transport_class = get_transport(type_=type_, name=name)
        transport = transport_class.from_config(config=config, **transport_config._asdict())
        return transport

    def _set_transport(self, api_name: str, transport: Transport, transport_type: str):
        self._registry.setdefault(api_name, self._RegistryEntry())
        self._registry[api_name] = self._registry[api_name]._replace(**{transport_type: transport})

    def _get_transport(self, api_name: str, transport_type: str, default=empty):
        registry_entry = self._registry.get(api_name)
        api_transport = None
        if registry_entry:
            api_transport = getattr(registry_entry, transport_type)

        if not api_transport and api_name != "default":
            try:
                api_transport = self._get_transport("default", transport_type)
            except TransportNotFound:
                pass

        if not api_transport and default == empty:
            raise TransportNotFound(
                f"No {transport_type} transport found for API '{api_name}'. Neither was a default "
                f"API transport found. Either specify a {transport_type} transport for this specific API, "
                f"or specify a default {transport_type} transport. In most cases setting a default transport "
                f"is the best course of action."
            )
        else:
            return api_transport

    def _get_transports(
        self, api_names: Sequence[str], transport_type: str
    ) -> List[Tuple[Transport, List[str]]]:
        apis_by_transport: Dict[Transport, List[str]] = {}
        for api_name in api_names:
            transport = self._get_transport(api_name, transport_type)
            apis_by_transport.setdefault(transport, [])
            apis_by_transport[transport].append(api_name)
        return list(apis_by_transport.items())

    def _has_transport(self, api_name: str, transport_type: str) -> bool:
        try:
            self._get_transport(api_name, transport_type)
        except TransportNotFound:
            return False
        else:
            return True

    def set_rpc_transport(self, api_name: str, transport):
        self._set_transport(api_name, transport, "rpc")

    def set_result_transport(self, api_name: str, transport):
        self._set_transport(api_name, transport, "result")

    def set_event_transport(self, api_name: str, transport):
        self._set_transport(api_name, transport, "event")

    def set_schema_transport(self, transport):
        self.schema_transport = transport

    def get_rpc_transport(self, api_name: str, default=empty) -> RpcTransport:
        return self._get_transport(api_name, "rpc", default=default)

    def get_result_transport(self, api_name: str, default=empty) -> ResultTransport:
        return self._get_transport(api_name, "result", default=default)

    def get_event_transport(self, api_name: str, default=empty) -> EventTransport:
        return self._get_transport(api_name, "event", default=default)

    def get_schema_transport(self, default=empty) -> SchemaTransport:
        if self.schema_transport or default != empty:
            return self.schema_transport or default
        else:
            # TODO: Link to docs
            raise TransportNotFound(
                "No schema transport is configured for this bus. Check your schema transport "
                "configuration is setup correctly (config section: bus.schema.transport)."
            )

    def has_rpc_transport(self, api_name: str) -> bool:
        return self._has_transport(api_name, "rpc")

    def has_result_transport(self, api_name: str) -> bool:
        return self._has_transport(api_name, "result")

    def has_event_transport(self, api_name: str) -> bool:
        return self._has_transport(api_name, "event")

    def has_schema_transport(self) -> bool:
        return bool(self.schema_transport)

    def get_rpc_transports(self, api_names: Sequence[str]) -> List[Tuple[RpcTransport, List[str]]]:
        """Get a mapping of transports to lists of APIs

        This is useful when multiple APIs can be served by a single transport
        """
        return self._get_transports(api_names, "rpc")

    def get_event_transports(
        self, api_names: Sequence[str]
    ) -> List[Tuple[EventTransport, List[str]]]:
        """Get a mapping of transports to lists of APIs

        This is useful when multiple APIs can be served by a single transport
        """
        return self._get_transports(api_names, "event")

    def get_all_transports(self) -> Set[Transport]:
        """Get a set of all transports irrespective of type"""
        all_transports = chain(*[entry._asdict().values() for entry in self._registry.values()])
        return set([t for t in all_transports if t is not None])


def get_available_transports(type_):
    loaded = load_entrypoint_classes(f"lightbus_{type_}_transports")

    return {name: class_ for module_name, name, class_ in loaded}


def get_transport(type_, name):
    for name_, class_ in get_available_transports(type_).items():
        if name == name_:
            return class_

    raise TransportNotFound(
        f"No '{type_}' transport found named '{name}'. Check the transport is installed and "
        f"has the relevant entrypoints setup in it's setup.py file. Or perhaps "
        f"you have a typo in your config file."
    )


def get_transport_name(cls: Type["Transport"]):
    for type_ in ("rpc", "result", "event"):
        for *_, name, class_ in load_entrypoint_classes(f"lightbus_{type_}_transports"):
            if cls == class_:
                return name

    raise TransportNotFound(
        f"Transport class {cls.__module__}.{cls.__name__} is not specified in any entrypoint."
    )
