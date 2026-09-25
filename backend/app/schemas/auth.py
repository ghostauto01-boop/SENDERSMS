"""
Pydantic schemas for authentication.
"""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Credentials are optional.

    The login screen has no password field any more — it is a single
    "Log in as admin" button, so the client posts an empty body. Both
    fields stay here so the old env/site-password logins keep working for
    anyone still calling them.
    """

    username: str | None = None
    password: str | None = None


class LoginResponse(BaseModel):
    success: bool
    user_id: int
    username: str
    role: str
    message: str


class SiteAccessUpdate(BaseModel):
    password_required: bool
    password: str | None = None


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str | None
    role: str
    is_active: bool

    model_config = {"from_attributes": True}
