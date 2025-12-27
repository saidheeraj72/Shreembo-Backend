from fastapi import HTTPException
from src.database.session import get_service_role_client
from uuid import UUID
from datetime import datetime, timezone
from typing import Optional, List

class AdminService:
    def __init__(self):
        self.supabase = get_service_role_client()

    # --- Branch Management ---
    def list_branches(self, org_id: UUID):
        try:
            res = self.supabase.table("branches").select("*").eq("org_id", org_id).execute()
            return res.data
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to list branches: {e}")

    def create_branch(self, org_id: UUID, name: str, 
                      branch_code: Optional[str] = None, branch_type: Optional[str] = None,
                      address: Optional[str] = None, city: Optional[str] = None,
                      state: Optional[str] = None, country: Optional[str] = None,
                      pincode: Optional[str] = None, phone: Optional[str] = None,
                      email: Optional[str] = None, manager_name: Optional[str] = None,
                      status: str = "active", location: Optional[str] = None):
        try:
            res = self.supabase.table("branches").insert({
                "org_id": str(org_id),
                "name": name,
                "location": location,
                "branch_code": branch_code,
                "branch_type": branch_type,
                "address": address,
                "city": city,
                "state": state,
                "country": country,
                "pincode": pincode,
                "phone": phone,
                "email": email,
                "manager_name": manager_name,
                "status": status
            }).execute()
            return res.data[0]
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to create branch: {e}")

    # --- User/Member Management ---
    def list_org_members(self, org_id: UUID):
        try:
            # Join organization_members with profiles and roles to get full details
            res = self.supabase.table("organization_members").select(
                "*, profiles(id, email, full_name, avatar_url), roles(id, name)"
            ).eq("org_id", org_id).execute()
            
            # Reformat data for easier consumption
            members = []
            for item in res.data:
                member_profile = item.pop("profiles")
                member_role = item.pop("roles")
                members.append({
                    "id": item["id"],
                    "user_id": member_profile["id"],
                    "email": member_profile["email"],
                    "full_name": member_profile["full_name"],
                    "avatar_url": member_profile["avatar_url"],
                    "role_id": member_role["id"] if member_role else None,
                    "role_name": member_role["name"] if member_role else None,
                    "status": item["status"],
                    "joined_at": item["joined_at"]
                })
            return members
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to list organization members: {e}")

    def invite_member(self, org_id: UUID, email: str, full_name: str, role_name: str = "Member"):
        # This is complex as it involves:
        # 1. Check if user already exists in auth.users
        # 2. If not, create user in auth.users (can send invite email from Supabase)
        # 3. Create profile entry if not exists
        # 4. Get role_id for the given role_name for this org
        # 5. Insert into organization_members
        raise HTTPException(status_code=501, detail="Invite member not yet implemented")

    def update_member_role(self, org_id: UUID, user_id: UUID, new_role_name: str):
        try:
            # Find the new role's ID for this organization
            role_res = self.supabase.table("roles").select("id").eq("org_id", org_id).eq("name", new_role_name).single().execute()
            if not role_res.data:
                raise HTTPException(status_code=404, detail=f"Role '{new_role_name}' not found in this organization.")
            new_role_id = role_res.data["id"]

            # Update the member's role in organization_members
            res = self.supabase.table("organization_members").update({"role_id": new_role_id}).eq("org_id", org_id).eq("user_id", user_id).execute()
            if not res.data:
                raise HTTPException(status_code=404, detail="Organization member not found.")
            return res.data[0]
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to update member role: {e}")

    def remove_member(self, org_id: UUID, user_id: UUID):
        try:
            # Remove from organization_members
            res = self.supabase.table("organization_members").delete().eq("org_id", org_id).eq("user_id", user_id).execute()
            if not res.data:
                raise HTTPException(status_code=404, detail="Organization member not found.")
            
            # Optionally: If user is only member of this org, update their profile.org_id to NULL or personal
            # For now, just remove membership. profile.org_id might become null or stay stale.
            return {"message": "Member removed successfully."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to remove member: {e}")

    # --- Role Management ---
    def list_roles(self, org_id: UUID):
        try:
            # 1. Fetch Roles
            res = self.supabase.table("roles").select("*").eq("org_id", org_id).order("name").execute()
            roles = res.data
            
            if not roles:
                return []

            role_ids = [r["id"] for r in roles]

            # 2. Fetch Permissions for these Roles
            # Join: role_permissions -> app_permissions -> app_modules
            perms_res = self.supabase.table("role_permissions").select(
                "role_id, app_permissions(action, app_modules(key))"
            ).in_("role_id", role_ids).execute()

            # 3. Build Permission Map: { role_id: { module_key: { action: bool } } }
            role_perms_map = {}
            for item in perms_res.data:
                r_id = item["role_id"]
                perm_info = item.get("app_permissions")
                if perm_info:
                    module_key = perm_info.get("app_modules", {}).get("key")
                    action = perm_info.get("action")
                    
                    if r_id not in role_perms_map:
                        role_perms_map[r_id] = self._get_default_permissions() # Initialize with defaults
                    
                    if module_key and action:
                        if module_key in role_perms_map[r_id]:
                            role_perms_map[r_id][module_key][action] = True

            # 4. Attach to Roles
            for r in roles:
                r["permissions"] = role_perms_map.get(r["id"], self._get_default_permissions())
                r["user_count"] = 0 # Placeholder for now

            return roles
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to list roles: {e}")

    def create_role(self, org_id: UUID, name: str, description: Optional[str] = None, color: str = "primary", permissions: dict = None):
        try:
            # 1. Create Role
            res = self.supabase.table("roles").insert({
                "org_id": str(org_id),
                "name": name,
                "description": description,
                "color": color
            }).execute()
            created_role = res.data[0]
            
            # 2. Save Permissions
            if permissions:
                self._save_role_permissions(created_role["id"], permissions)
                created_role["permissions"] = permissions
            else:
                created_role["permissions"] = self._get_default_permissions()

            created_role["user_count"] = 0
            return created_role
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to create role: {e}")

    def update_role(self, org_id: UUID, role_id: UUID, name: Optional[str] = None, description: Optional[str] = None, color: Optional[str] = None, permissions: dict = None):
        try:
            # Verify role belongs to this org
            role_check = self.supabase.table("roles").select("id").eq("id", role_id).eq("org_id", org_id).execute()
            if not role_check.data:
                raise HTTPException(status_code=404, detail="Role not found in this organization.")

            # 1. Update Role Fields
            update_data = {}
            if name is not None: update_data["name"] = name
            if description is not None: update_data["description"] = description
            if color is not None: update_data["color"] = color

            if update_data:
                res = self.supabase.table("roles").update(update_data).eq("id", role_id).execute()
                updated_role = res.data[0]
            else:
                res = self.supabase.table("roles").select("*").eq("id", role_id).single().execute()
                updated_role = res.data

            # 2. Update Permissions
            if permissions:
                self._save_role_permissions(role_id, permissions)
                updated_role["permissions"] = permissions
            else:
                # If not updating permissions, ideally we should fetch existing ones to return complete object
                # But for now returning default or what we have is acceptable if frontend handles it
                updated_role["permissions"] = self._get_default_permissions() 

            updated_role["user_count"] = 0
            return updated_role
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to update role: {e}")

    def _get_permission_lookup(self):
        """Returns { module_key: { action: permission_id } }"""
        res = self.supabase.table("app_permissions").select("id, action, app_modules(key)").execute()
        lookup = {}
        for item in res.data:
            module_key = item.get("app_modules", {}).get("key")
            action = item.get("action")
            if module_key and action:
                if module_key not in lookup:
                    lookup[module_key] = {}
                lookup[module_key][action] = item["id"]
        return lookup

    def _save_role_permissions(self, role_id: UUID, permissions: dict):
        # 1. Get ID lookup
        lookup = self._get_permission_lookup()
        
        # 2. Collect all permission IDs to be assigned
        permission_ids = []
        for module_key, actions in permissions.items():
            if module_key in lookup:
                for action, is_granted in actions.items():
                    if is_granted and action in lookup[module_key]:
                        permission_ids.append(lookup[module_key][action])
        
        # 3. Transactional update (delete all then insert new)
        # Note: Supabase-py doesn't support complex transactions easily in one go without RPC, 
        # so we do delete then insert.
        self.supabase.table("role_permissions").delete().eq("role_id", role_id).execute()
        
        if permission_ids:
            insert_data = [{"role_id": str(role_id), "permission_id": pid} for pid in permission_ids]
            self.supabase.table("role_permissions").insert(insert_data).execute()

    def delete_role(self, org_id: UUID, role_id: UUID):
        try:
            # Verify role belongs to this org
            role_check = self.supabase.table("roles").select("id, is_system_role").eq("id", role_id).eq("org_id", org_id).execute()
            if not role_check.data:
                raise HTTPException(status_code=404, detail="Role not found in this organization.")

            if role_check.data[0].get("is_system_role"):
                raise HTTPException(status_code=400, detail="Cannot delete system roles.")

            # Delete the role (cascade will handle role_permissions)
            self.supabase.table("roles").delete().eq("id", role_id).execute()
            return {"message": "Role deleted successfully."}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to delete role: {e}")

    def _get_default_permissions(self):
        return {
            "documents": {"view": False, "create": False, "edit": False, "delete": False, "share": False},
            "branches": {"view": False, "create": False, "edit": False, "delete": False, "manage": False},
            "users": {"view": False, "create": False, "edit": False, "delete": False, "assign": False},
            "audit": {"view": False, "export": False},
            "settings": {"view": False, "edit": False}
        }

    # --- User Permission Overrides ---
    def get_member_permissions(self, org_id: UUID, user_id: UUID):
        """
        Get a member's custom permissions (permission overrides beyond their role).
        Returns permissions as a structured dict similar to role permissions.
        """
        try:
            # Verify user is a member of this org
            member_check = self.supabase.table("organization_members").select("id").eq("org_id", org_id).eq("user_id", user_id).execute()
            if not member_check.data:
                raise HTTPException(status_code=404, detail="User is not a member of this organization.")

            # Get user's custom permissions with module info
            res = self.supabase.table("user_permissions").select(
                "permission_id, app_permissions(id, action, app_modules(key))"
            ).eq("user_id", user_id).execute()

            # Convert to structured permission dict
            custom_permissions = self._get_default_permissions()
            for item in res.data:
                perm = item.get("app_permissions")
                if perm:
                    module_key = perm.get("app_modules", {}).get("key")
                    action = perm.get("action")
                    if module_key and action and module_key in custom_permissions:
                        custom_permissions[module_key][action] = True

            return {
                "user_id": str(user_id),
                "custom_permissions": custom_permissions
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get member permissions: {e}")

    def update_member_permissions(self, org_id: UUID, user_id: UUID, permissions: dict):
        """
        Update a member's custom permissions.
        Permissions is a dict like: {"documents": {"view": true, "create": true}, ...}
        """
        try:
            # Verify user is a member of this org
            member_check = self.supabase.table("organization_members").select("id").eq("org_id", org_id).eq("user_id", user_id).execute()
            if not member_check.data:
                raise HTTPException(status_code=404, detail="User is not a member of this organization.")

            # Get all app_permissions with their module keys
            all_perms_res = self.supabase.table("app_permissions").select(
                "id, action, app_modules(key)"
            ).execute()

            # Build a lookup: {module_key: {action: permission_id}}
            perm_lookup = {}
            for p in all_perms_res.data:
                module_key = p.get("app_modules", {}).get("key")
                action = p.get("action")
                if module_key and action:
                    if module_key not in perm_lookup:
                        perm_lookup[module_key] = {}
                    perm_lookup[module_key][action] = p["id"]

            # Delete existing custom permissions for this user
            self.supabase.table("user_permissions").delete().eq("user_id", user_id).execute()

            # Insert new permissions based on the provided dict
            permissions_to_insert = []
            for module_key, actions in permissions.items():
                if module_key in perm_lookup:
                    for action, is_granted in actions.items():
                        if is_granted and action in perm_lookup[module_key]:
                            permissions_to_insert.append({
                                "user_id": str(user_id),
                                "permission_id": perm_lookup[module_key][action]
                            })

            if permissions_to_insert:
                self.supabase.table("user_permissions").insert(permissions_to_insert).execute()

            return {
                "message": "Permissions updated successfully.",
                "user_id": str(user_id),
                "custom_permissions": permissions
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to update member permissions: {e}")

admin_service = AdminService()
