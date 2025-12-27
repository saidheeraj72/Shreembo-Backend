from src.services.admin_service import admin_service
from src.database.session import get_service_role_client
import sys

def test_create_branch():
    supabase = get_service_role_client()
    
    # 1. Get an existing Organization
    org_res = supabase.table("organizations").select("id").limit(1).execute()
    if not org_res.data:
        print("No organizations found. Cannot test branch creation.")
        return

    org_id = org_res.data[0]["id"]
    print(f"Testing with Org ID: {org_id}")

    # 2. Create Branch with new fields
    branch_data = {
        "name": "Test Branch HQ",
        "branch_code": "TST-001",
        "branch_type": "headquarters",
        "address": "123 Tech Park",
        "city": "Cyber City",
        "state": "State",
        "country": "Nation",
        "pincode": "123456",
        "phone": "+1 555 0123",
        "email": "branch@test.com",
        "manager_name": "John Doe",
        "status": "active"
    }

    try:
        new_branch = admin_service.create_branch(
            org_id=org_id, 
            **branch_data
        )
        print("Successfully created branch with new fields:")
        print(new_branch)
        
        # Cleanup
        supabase.table("branches").delete().eq("id", new_branch["id"]).execute()
        print("Cleaned up test branch.")
        
    except Exception as e:
        print("\n!!! TEST FAILED !!!")
        print(f"Error: {e}")
        print("\nThis failure is EXPECTED if you have not run the schema update SQL.")
        print("Please execute the contents of 'update_branches_schema.sql' in your Supabase SQL Editor.")

if __name__ == "__main__":
    test_create_branch()
