from fastapi import HTTPException
from src.database.session import get_service_role_client
from datetime import datetime, timezone

class SuperAdminService:
    """
    Handles operations for Global Super Admins who oversee ALL organizations.
    """
    def __init__(self):
        self.supabase = get_service_role_client()

    def is_super_admin(self, email: str) -> bool:
        """
        Checks if the given email exists in the super_admins table.
        """
        try:
            res = self.supabase.table("super_admins").select("id").eq("email", email).execute()
            return len(res.data) > 0
        except Exception:
            return False

    def get_requests(self, status: str = None):
        """
        Fetch organization requests. Optionally filter by status.
        """
        query = self.supabase.table("organization_requests").select("*")
        if status:
            query = query.eq("status", status)
        
        res = query.order("created_at", desc=True).execute()
        return res.data

    def get_all_organizations(self):
        """
        Fetch all organizations with their owner details (if possible via join, or just raw orgs).
        """
        # Join with profiles to get owner name? Supabase join syntax: select("*, profiles(full_name)")
        # Assuming simple select for now.
        res = self.supabase.table("organizations").select("*").order("created_at", desc=True).execute()
        return res.data

    def approve_org_request(self, request_id: str):
        # 1. Get the request
        req_res = self.supabase.table("organization_requests").select("*").eq("id", request_id).single().execute()
        if not req_res.data:
            raise HTTPException(status_code=404, detail="Request not found")
        
        request = req_res.data
        if request["status"] != "pending":
            raise HTTPException(status_code=400, detail=f"Request is already {request['status']}")

        user_id = request["user_id"]
        org_name = request["org_name"]
        
        # 2. Create Organization
        # Slug generation (simple version - in prod, ensure uniqueness)
        slug = org_name.lower().replace(" ", "-")
        
        org_data = {
            "name": org_name,
            "slug": slug,
            "plan_type": "team", 
            "owner_id": user_id
        }
        
        try:
            org_res = self.supabase.table("organizations").insert(org_data).execute()
            # If insert returns list
            if isinstance(org_res.data, list) and len(org_res.data) > 0:
                new_org = org_res.data[0]
            else:
                new_org = org_res.data
                
            new_org_id = new_org["id"]

            # 3. Create Default Roles
            roles_data = [
                {"org_id": new_org_id, "name": "Owner", "description": "Organization Owner", "is_system_role": True},
                {"org_id": new_org_id, "name": "Admin", "description": "Organization Administrator", "is_system_role": True},
                {"org_id": new_org_id, "name": "Member", "description": "Standard Member", "is_system_role": True}
            ]
            roles_res = self.supabase.table("roles").insert(roles_data).execute()
            
            # Get Owner Role ID
            owner_role_id = next((r["id"] for r in roles_res.data if r["name"] == "Owner"), None)

            # 4. Add User to Organization Members
            self.supabase.table("organization_members").insert({
                "org_id": new_org_id,
                "user_id": user_id,
                "role_id": owner_role_id,
                "status": "active"
            }).execute()

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to create organization/roles: {str(e)}")

        # 5. Update User Profile -> Link to new Org (Legacy/Current Context)
        try:
            self.supabase.table("profiles").update({
                "org_id": new_org_id,
                "account_type": "organization",
                "status": "active" 
            }).eq("id", user_id).execute()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to update profile: {str(e)}")
            
        # 6. Update Request Status
        self.supabase.table("organization_requests").update({
            "status": "approved",
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", request_id).execute()

        return {"message": "Organization approved and created", "org_id": new_org_id}

    def reject_org_request(self, request_id: str, reason: str):
        self.supabase.table("organization_requests").update({
            "status": "rejected",
            "rejection_reason": reason,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", request_id).execute()
        
        return {"message": "Request rejected"}

super_admin_service = SuperAdminService()
