from json import JSONEncoder

from enum import Enum

from lightbus.utilities.frozendict import frozendict


class LightbusEncoder(JSONEncoder):
    """Custom JSON encode to handle encoding some extra types"""

    def default(self, o):
        if isinstance(o, frozendict):
            return o._dict
        elif isinstance(o, Enum):
            return o.value
        else:
            super(LightbusEncoder, self).default(o)


def json_encode(obj, indent=2, sort_keys=True, **options):
    return LightbusEncoder(indent=indent, sort_keys=sort_keys, **options).encode(obj)
