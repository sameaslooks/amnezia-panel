# schemas.py
from pydantic import BaseModel
from typing import Optional, List, Dict


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    role: str


class ClientCreate(BaseModel):
    name: str
    user_id: Optional[int] = None


class ExpiryDateRequest(BaseModel):
    expiry_date: Optional[str] = None


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"


class UserUpdate(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    config_limit: Optional[int] = None


class ServerCreate(BaseModel):
    name: str
    host: Optional[str] = None
    port: int = 22
    username: Optional[str] = None
    auth_type: str = "local"
    password: Optional[str] = None
    private_key: Optional[str] = None


class ServerUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    auth_type: Optional[str] = None
    password: Optional[str] = None
    private_key: Optional[str] = None
    is_active: Optional[bool] = None


class ServerStatusItem(BaseModel):
    id: int
    name: str
    is_active: bool
    auth_type: str
    status: Dict


class DashboardRequest(BaseModel):
    server_statuses: List[ServerStatusItem]