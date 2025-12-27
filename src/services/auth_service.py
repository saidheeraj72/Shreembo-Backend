from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status
from src.database.session import get_service_role_client
from uuid import UUID 
from typing import Optional, List 
from src.schemas.auth import UserSignup, OrgRegistration, UserLogin, Token 
from src.services.super_admin_service import super_admin_service

class AuthService:
    def __init__(self):
        self.supabase = get_service_role_client()

    def signup_personal(self, data: UserSignup):
        # 1. Create Auth User
        try:
            auth_response = self.supabase.auth.admin.create_user({
                "email": data.email,
                "password": data.password,
                "email_confirm": True, 
                "user_metadata": {"full_name": data.full_name}
            })
            user_id = auth_response.user.id
        except Exception as e:
            if "User already registered" in str(e):
                raise HTTPException(status_code=400, detail="User already registered")
            raise HTTPException(status_code=500, detail=str(e))

        # 2. Check for Domain Match
        email_domain = data.email.split("@")[-1]
        
        # Naive domain check - in prod, ensure domains are verified/unique
        org_res = self.supabase.table("organizations").select("id").eq("domain", email_domain).execute()
        
        if org_res.data and len(org_res.data) > 0:
            # === JOIN EXISTING ORGANIZATION ===
            org_id = org_res.data[0]["id"]
            
            # Find 'Member' role for this org
            role_res = self.supabase.table("roles").select("id").eq("org_id", org_id).eq("name", "Member").execute()
            role_id = role_res.data[0]["id"] if role_res.data else None
            
            # Create Profile linked to Org
            profile_data = {
                "id": user_id,
                "email": data.email,
                "full_name": data.full_name,
                "account_type": "organization",
                "org_id": org_id, # Set as current context
                "status": "active"
            }
            
            try:
                self.supabase.table("profiles").insert(profile_data).execute()
                
                # Add to Organization Members
                self.supabase.table("organization_members").insert({
                    "org_id": org_id,
                    "user_id": user_id,
                    "role_id": role_id,
                    "status": "active"
                }).execute()
                
                return {"message": f"Account created. You have joined the organization matching your domain: {email_domain}", "user_id": user_id}
                
            except Exception as e:
                self.supabase.auth.admin.delete_user(user_id)
                raise HTTPException(status_code=500, detail=f"Failed to join organization: {str(e)}")

        else:
            # === CREATE PERSONAL ACCOUNT ===
            expiry_date = datetime.now(timezone.utc) + timedelta(days=14)
            
            profile_data = {
                "id": user_id,
                "email": data.email,
                "full_name": data.full_name,
                "account_type": "personal",
                "subscription_expiry": expiry_date.isoformat(),
                "status": "active"
            }

            try:
                self.supabase.table("profiles").insert(profile_data).execute()
            except Exception as e:
                self.supabase.auth.admin.delete_user(user_id)
                raise HTTPException(status_code=500, detail=f"Failed to create profile: {str(e)}")

            return {"message": "Personal account created successfully", "user_id": user_id, "expires_at": expiry_date}

    def register_organization(self, data: OrgRegistration):
        # 1. Create Auth User (if not exists) or get ID
        user_id = None
        try:
            # Try creating
            auth_response = self.supabase.auth.admin.create_user({
                "email": data.email,
                "password": data.password,
                "email_confirm": True,
                "user_metadata": {"full_name": data.full_name}
            })
            user_id = auth_response.user.id
            
            # Create basic profile for them (inactive or pending status maybe?)
            # For now, we create a profile but NOT linked to an org yet.
            profile_data = {
                "id": user_id,
                "email": data.email,
                "full_name": data.full_name,
                "account_type": "personal", # Temporary until approved
                "status": "active"
            }
            self.supabase.table("profiles").insert(profile_data).execute()
            
        except Exception as e:
            if "User already registered" in str(e):
                # If user exists, we need to get their ID. 
                # Ideally, we should check if they can upgrade. For now, we assume fresh flows or proceed.
                # Getting user by email (Admin API)
                # NOTE: Supabase Python Admin API might differ slightly in method names depending on version.
                # Assuming we can't easily get ID from error, we might need to list users or ask them to login first.
                # For simplicity in this 'from scratch' plan, we assume new user.
                raise HTTPException(status_code=400, detail="User already registered. Please login to upgrade account.")
            else:
                raise HTTPException(status_code=500, detail=str(e))

        # 2. Create Organization Request
        request_data = {
            "user_id": user_id,
            "user_email": data.email,
            "user_full_name": data.full_name,
            "org_name": data.org_name,
            "status": "pending"
        }
        
        try:
            self.supabase.table("organization_requests").insert(request_data).execute()
        except Exception as e:
             raise HTTPException(status_code=500, detail=f"Failed to create request: {str(e)}")

        return {"message": "Organization request submitted for approval", "request_id": "check_db"}

    def login(self, data: UserLogin):
        try:
            # Use standard client for login (or auth.sign_in_with_password)
            # We use service client here just for consistency in class, but login usually needs public client
            # actually better to use the public client for login to get the right session
            from src.database.session import get_supabase_client
            public_client = get_supabase_client()
            
            auth_response = public_client.auth.sign_in_with_password({
                "email": data.email,
                "password": data.password
            })
            
            user = auth_response.user
            session = auth_response.session
            
            # Fetch Profile to return account type
            profile_res = self.supabase.table("profiles").select("*").eq("id", user.id).single().execute()
            profile = profile_res.data
            
            return {
                "access_token": session.access_token,
                "token_type": "bearer",
                "user_id": user.id,
                "account_type": profile.get("account_type"),
                "org_id": profile.get("org_id"),
                "full_name": profile.get("full_name"),
                "email": profile.get("email"),
                "avatar_url": profile.get("avatar_url")
            }
            
        except Exception as e:
            raise HTTPException(status_code=401, detail="Invalid credentials")

    def get_user_profile(self, user_id: UUID):
        try:
            profile_res = self.supabase.table("profiles").select("id, email, full_name, avatar_url, org_id, account_type").eq("id", user_id).single().execute()
            return profile_res.data
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch user profile: {e}")

    def get_my_permissions(self, user_id: str, org_id: Optional[str], email: str):
        try:
            effective_permissions = self._get_default_permissions()

            # Check Super Admin Status
            if super_admin_service.is_super_admin(email):
                effective_permissions["super_admin"]["access"] = True

            # Helper to merge
            def merge_perms(rows):
                for item in rows:
                    perm = item.get("app_permissions")
                    if perm:
                        module_key = perm.get("app_modules", {}).get("key")
                        action = perm.get("action")
                        if module_key and action and module_key in effective_permissions:
                            effective_permissions[module_key][action] = True

            # 1. Get User's Role in Org (Only if org_id is present)
            if org_id:
                try:
                    member_res = self.supabase.table("organization_members").select("role_id").eq("org_id", org_id).eq("user_id", user_id).single().execute()
                    if member_res.data:
                        role_id = member_res.data["role_id"]
                        # 2. Get Role Permissions
                        role_perms_res = self.supabase.table("role_permissions").select(
                            "app_permissions(action, app_modules(key))"
                        ).eq("role_id", role_id).execute()
                        merge_perms(role_perms_res.data)
                except Exception:
                     # Ignore if member lookup fails
                     pass

            # 3. Get User Custom Permissions
            user_perms_res = self.supabase.table("user_permissions").select(
                "app_permissions(action, app_modules(key))"
            ).eq("user_id", user_id).execute()

            merge_perms(user_perms_res.data)

            return effective_permissions
        except Exception as e:
            # Log error?
            print(f"Error getting permissions: {e}")
            return self._get_default_permissions()

    def _get_default_permissions(self):
        return {
            "documents": {"view": False, "create": False, "edit": False, "delete": False, "share": False},
            "branches": {"view": False, "create": False, "edit": False, "delete": False, "manage": False},
            "users": {"view": False, "create": False, "edit": False, "delete": False, "assign": False},
            "audit": {"view": False, "export": False},
            "settings": {"view": False, "edit": False},
            "super_admin": {"access": False}
        }

auth_service = AuthService()