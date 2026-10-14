from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field

class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    name: str
    role: Literal['admin','engineer','analyst','viewer']
    organization_id: UUID
    organization_name: str
    preferences: dict

class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    display_name: str = Field(min_length=1, max_length=100)
    page_size: Literal[10,25,50] = 25
    compact_tables: bool = False

class CreateUser(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: EmailStr
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=128)
    role: Literal['admin','engineer','analyst','viewer'] = 'viewer'

class OrganizationUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
