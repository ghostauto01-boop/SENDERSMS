"""
Pydantic schemas for authentication.
"""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    success: bool
    user_id: int
    username: str
    role: str
    message: str


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str | None
    role: str
    is_active: bool

    model_config = {"from_attributes": True}
