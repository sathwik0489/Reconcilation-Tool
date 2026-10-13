from typing import Literal,Any
from uuid import UUID
from pydantic import Field,model_validator
from app.schemas.integrations import Strict

class MapColumn(Strict):
    source: str=Field(min_length=1,max_length=255)
    target: str=Field(min_length=1,max_length=128)
    transformation: Literal['exact','trim','lower','upper']='exact'

class Filter(Strict):
    column: str=Field(min_length=1,max_length=255)
    operator: Literal['eq','ne','gt','gte','lt','lte','not_null']='eq'
    value: Any=None

class Aggregate(Strict):
    source: str=Field(min_length=1,max_length=255)
    target: str=Field(min_length=1,max_length=128)
    function: Literal['COUNT','SUM','MIN','MAX','AVG']='SUM'

class Join(Strict):
    left: str=Field(min_length=1,max_length=255)
    right: str=Field(min_length=1,max_length=255)

class Branch(Strict):
    source_table_ids: list[UUID]=Field(min_length=1,max_length=5)
    target_table_id: UUID
    columns: list[MapColumn]=Field(min_length=1,max_length=100)
    business_keys: list[str]=Field(min_length=1,max_length=10)
    source_filters: list[Filter]=Field(default_factory=list,max_length=20)
    target_filters: list[Filter]=Field(default_factory=list,max_length=20)
    joins: list[Join]=Field(default_factory=list,max_length=4)
    aggregations: list[Aggregate]=Field(default_factory=list,max_length=20)
    numeric_tolerance: float=Field(default=0,ge=0,le=1000000,allow_inf_nan=False)
    timestamp_tolerance_seconds: int=Field(default=0,ge=0,le=86400)
    case_sensitive: bool=True
    nulls_equal: bool=True
    normalize_types: bool=True
    required_columns: list[str]=Field(default_factory=list,max_length=100)
    @model_validator(mode='after')
    def unique(self):
        if len(set(self.source_table_ids))!=len(self.source_table_ids):raise ValueError('Repeated source table')
        targets=[c.target for c in self.columns]+[a.target for a in self.aggregations]
        if len(targets)!=len(set(targets)):raise ValueError('Each target column must have one mapping or aggregation')
        if not set(self.business_keys).issubset(targets):raise ValueError('Business keys must use mapped target column names')
        if len(self.source_table_ids)>1 and len(self.joins)!=len(self.source_table_ids)-1:raise ValueError('Many-to-one requires one join per additional source')
        return self

class MappingInput(Strict):
    name: str=Field(min_length=1,max_length=100)
    branches: list[Branch]=Field(min_length=1,max_length=10)
    @model_validator(mode='after')
    def cardinality(self):
        if len(set(b.target_table_id for b in self.branches))!=len(self.branches):raise ValueError('Repeated target branch')
        if len(self.branches)>1 and (any(len(b.source_table_ids)!=1 for b in self.branches) or len(set(b.source_table_ids[0] for b in self.branches))!=1):
            raise ValueError('One-to-many must use the same single source on each target branch')
        return self

class RunInput(Strict):
    mapping_id: UUID
