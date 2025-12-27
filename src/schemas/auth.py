from pydantic import BaseModel, EmailStr
from typing import Optional
from uuid import UUID

class UserSignup(BaseModel):
    email: EmailStr
    password: str
    full_name: str

class OrgRegistration(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    org_name: str
    org_slug: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str
    user_id: UUID
    account_type: Optional[str] = None
    org_id: Optional[UUID] = None
    full_name: Optional[str] = None
    email: Optional[str] = None
    avatar_url: Optional[str] = None

class OrgRequestUpdate(BaseModel):
    status: str # 'approved' or 'rejected'
    rejection_reason: Optional[str] = None
