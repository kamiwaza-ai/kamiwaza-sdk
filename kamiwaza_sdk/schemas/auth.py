# kamiwaza_client/schemas/auth.py

from pydantic import BaseModel, Field, EmailStr, HttpUrl
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID

class IDPConfig(BaseModel):
    provider: str = Field(description="Name of the identity provider (e.g., 'Auth0', 'Okta', 'Azure AD')")
    domain: str = Field(description="Domain of the identity provider")
    client_id: str = Field(description="Client ID for the identity provider")
    metadata_url: Optional[HttpUrl] = Field(default=None, description="URL for fetching IDP metadata")
    additional_config: Dict[str, Any] = Field(default_factory=dict, description="Additional provider-specific configuration")

class Right(BaseModel):
    id: UUID
    name: str = Field(description="Name of the right")
    description: Optional[str] = Field(default=None, description="Description of the right")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class RightCreate(BaseModel):
    name: str
    description: Optional[str] = None

class RightUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None

class Role(BaseModel):
    id: UUID
    name: str = Field(description="Name of the role")
    description: Optional[str] = Field(default=None, description="Description of the role")
    rights: List[UUID] = Field(default_factory=list, description="List of right IDs associated with this role")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class RoleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    rights: List[UUID] = Field(default_factory=list)

class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    rights: Optional[List[UUID]] = None

class Group(BaseModel):
    id: UUID
    name: str = Field(description="Name of the group")
    description: Optional[str] = Field(default=None, description="Description of the group")
    roles: List[UUID] = Field(default_factory=list, description="List of role IDs associated with this group")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class GroupCreate(BaseModel):
    name: str
    description: Optional[str] = None
    roles: List[UUID] = Field(default_factory=list)

class GroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    roles: Optional[List[UUID]] = None

class Organization(BaseModel):
    id: UUID
    name: str = Field(description="Name of the organization")
    description: Optional[str] = Field(default=None, description="Description of the organization")
    idp_config: Optional[IDPConfig] = Field(default=None, description="IDP configuration for this organization")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class OrganizationCreate(BaseModel):
    name: str
    description: Optional[str] = None
    idp_config: Optional[IDPConfig] = None

class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    idp_config: Optional[IDPConfig] = None

class UserBase(BaseModel):
    username: str = Field(description="Username of the user")
    email: EmailStr = Field(description="Email of the user")
    full_name: Optional[str] = Field(default=None, description="Full name of the user")
    organization_id: Optional[UUID] = Field(default=None, description="ID of the organization the user belongs to")
    is_superuser: bool = Field(default=False, description="Whether the user is a superuser")
    external_id: Optional[str] = Field(default=None, description="External ID from the IDP")

class UserCreate(UserBase):
    password: Optional[str] = Field(default=None, description="Password for user creation (required for local users)")
    groups: List[UUID] = Field(default_factory=list, description="List of group IDs the user belongs to")

class LocalUserCreate(UserCreate):
    password: str = Field(description="Password required for local user creation")

class UserUpdate(BaseModel):
    email: Optional[EmailStr] = Field(default=None, description="Email of the user")
    full_name: Optional[str] = Field(default=None, description="Full name of the user")
    is_active: Optional[bool] = Field(default=None, description="Whether the user is active")
    groups: Optional[List[UUID]] = Field(default=None, description="List of group IDs the user belongs to")
    is_superuser: Optional[bool] = Field(default=None, description="Whether the user is a superuser")
    organization_id: Optional[UUID] = Field(default=None, description="ID of the organization the user belongs to")

class User(UserBase):
    id: UUID = Field(description="Unique identifier for the user")
    is_active: bool = Field(default=True, description="Whether the user is active")
    groups: List[UUID] = Field(default_factory=list, description="List of group IDs the user belongs to")
    created_at: datetime = Field(description="Timestamp of user creation")
    updated_at: Optional[datetime] = Field(default=None, description="Timestamp of last user update")
    last_login: Optional[datetime] = Field(default=None, description="Last login timestamp")

class UserPermissions(BaseModel):
    user_id: UUID = Field(description="ID of the user")
    rights: List[str] = Field(default_factory=list, description="List of rights assigned to the user")
    roles: List[str] = Field(default_factory=list, description="List of roles assigned to the user")
    groups: List[str] = Field(default_factory=list, description="List of groups the user belongs to")

class Token(BaseModel):
    access_token: str = Field(description="JWT access token")
    token_type: str = Field(description="Token type (e.g., 'bearer')")
    expires_in: int = Field(description="Token expiration time in seconds")
    refresh_token: Optional[str] = Field(default=None, description="Refresh token for obtaining a new access token")
    id_token: Optional[str] = Field(default=None, description="ID token (used in OpenID Connect)")

model_config = {
    "from_attributes": True
}