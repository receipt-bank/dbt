import abc
from dataclasses import dataclass, field
from typing import (
    Any, ClassVar, Dict, Tuple, Iterable, Optional, NewType
)

from hologram import JsonSchemaMixin
from hologram.helpers import (
    StrEnum, register_pattern, ExtensibleJsonSchemaMixin
)

from dbt.contracts.util import Replaceable
from dbt.utils import translate_aliases


Identifier = NewType('Identifier', str)
register_pattern(Identifier, r'^[A-Za-z_][A-Za-z0-9_]+$')


class ConnectionState(StrEnum):
    INIT = 'init'
    OPEN = 'open'
    CLOSED = 'closed'
    FAIL = 'fail'


@dataclass(init=False)
class Connection(ExtensibleJsonSchemaMixin, Replaceable):
    type: Identifier
    name: Optional[str]
    _credentials: JsonSchemaMixin = None  # underscore to prevent serialization
    state: ConnectionState = ConnectionState.INIT
    transaction_open: bool = False
    _handle: Optional[Any] = None  # underscore to prevent serialization

    def __init__(
        self,
        type: Identifier,
        name: Optional[str],
        credentials: JsonSchemaMixin,
        state: ConnectionState = ConnectionState.INIT,
        transaction_open: bool = False,
        handle: Optional[Any] = None,
    ) -> None:
        self.type = type
        self.name = name
        self.credentials = credentials
        self.state = state
        self.transaction_open = transaction_open
        self.handle = handle

    @property
    def credentials(self):
        return self._credentials

    @credentials.setter
    def credentials(self, value):
        self._credentials = value

    @property
    def handle(self):
        return self._handle

    @handle.setter
    def handle(self, value):
        self._handle = value


# see https://github.com/python/mypy/issues/4717#issuecomment-373932080
# and https://github.com/python/mypy/issues/5374
# for why we have type: ignore. Maybe someday dataclasses + abstract classes
# will work.
@dataclass
class Credentials(  # type: ignore
    ExtensibleJsonSchemaMixin,
    Replaceable,
    metaclass=abc.ABCMeta
):
    database: str
    schema: str
    _ALIASES: ClassVar[Dict[str, str]] = field(default={}, init=False)

    @abc.abstractproperty
    def type(self) -> str:
        raise NotImplementedError(
            'type not implemented for base credentials class'
        )

    def connection_info(self) -> Iterable[Tuple[str, Any]]:
        """Return an ordered iterator of key/value pairs for pretty-printing.
        """
        as_dict = self.to_dict()
        for key in self._connection_keys():
            if key in as_dict:
                yield key, as_dict[key]

    @abc.abstractmethod
    def _connection_keys(self) -> Tuple[str, ...]:
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data):
        data = cls.translate_aliases(data)
        return super().from_dict(data)

    @classmethod
    def translate_aliases(cls, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return translate_aliases(kwargs, cls._ALIASES)

    def to_dict(self, omit_none=True, validate=False, with_aliases=False):
        serialized = super().to_dict(omit_none=omit_none, validate=validate)
        if with_aliases:
            serialized.update({
                new_name: serialized[canonical_name]
                for new_name, canonical_name in self._ALIASES.items()
                if canonical_name in serialized
            })
        return serialized
