"""Provider-independent facts about economically owned, spendable assets."""
from dataclasses import dataclass
from typing import Any, Mapping

from .operation import amount
from .time import normalize_timestamp


@dataclass(frozen=True)
class FundingAvailability:
    available_value_usd: float
    restricted_value_usd: float
    unknown_value_usd: float
    observed_at: str
    source: str

    def __post_init__(self):
        for name in ('available_value_usd','restricted_value_usd','unknown_value_usd'):
            object.__setattr__(self,name,amount(getattr(self,name),name))
        object.__setattr__(self,'observed_at',normalize_timestamp(self.observed_at))
        if not isinstance(self.source,str) or not self.source.strip():
            raise ValueError('funding source is required')

    @property
    def total_value_usd(self):
        return self.available_value_usd+self.restricted_value_usd+self.unknown_value_usd

    def as_dict(self):
        return {name:getattr(self,name) for name in self.__dataclass_fields__}

    @classmethod
    def from_mapping(cls,value:Mapping[str,Any]):
        if not isinstance(value,Mapping) or set(value)!=set(cls.__dataclass_fields__):
            raise ValueError('funding availability requires exactly the canonical fields')
        return cls(**value)
