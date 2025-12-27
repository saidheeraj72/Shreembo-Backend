#!/usr/bin/env python3
"""
Script to create a super admin user.
Run this after applying the database schema.

Usage:
    python scripts/create_super_admin.py your-email@example.com "Your Name"
"""
import sys
import os
import asyncio

# Add parent directory to path to import from src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.database import db


async def create_super_admin(email: str, full_name: str = None):
    """Create a super admin user."""
    try:
        # Check if already exists
        existing = (
            db.admin.table("super_admins")
            .select("id, email")
            .eq("email", email.lower())
            .maybe_single()
            .execute()
        )

        if existing.data:
            print(f"✅ Super admin already exists: {email}")
            print(f"   ID: {existing.data['id']}")
            return existing.data

        # Create new super admin
        data = {
            "email": email.lower(),
            "full_name": full_name,
            "is_active": True,
        }

        response = db.admin.table("super_admins").insert(data).execute()

        if response.data:
            print(f"✅ Super admin created successfully!")
            print(f"   Email: {response.data[0]['email']}")
            print(f"   Name: {response.data[0].get('full_name', 'N/A')}")
            print(f"   ID: {response.data[0]['id']}")
            return response.data[0]
        else:
            print("❌ Failed to create super admin")
            return None

    except Exception as e:
        print(f"❌ Error: {e}")
        return None


async def list_super_admins():
    """List all super admins."""
    try:
        response = (
            db.admin.table("super_admins")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )

        if response.data:
            print(f"\n📋 Current Super Admins ({len(response.data)}):")
            print("-" * 70)
            for admin in response.data:
                status = "✅ Active" if admin.get("is_active") else "❌ Inactive"
                print(f"{status} | {admin['email']:<30} | {admin.get('full_name', 'N/A'):<20}")
            print("-" * 70)
        else:
            print("\n⚠️  No super admins found")

    except Exception as e:
        print(f"❌ Error listing super admins: {e}")


def main():
    """Main function."""
    if len(sys.argv) < 2:
        print("Usage: python scripts/create_super_admin.py <email> [full_name]")
        print("\nExamples:")
        print('  python scripts/create_super_admin.py admin@example.com')
        print('  python scripts/create_super_admin.py admin@example.com "Admin User"')
        print("\nOr to list all super admins:")
        print("  python scripts/create_super_admin.py --list")
        sys.exit(1)

    if sys.argv[1] == "--list":
        asyncio.run(list_super_admins())
        sys.exit(0)

    email = sys.argv[1]
    full_name = sys.argv[2] if len(sys.argv) > 2 else None

    # Validate email format
    if "@" not in email:
        print("❌ Invalid email address")
        sys.exit(1)

    print(f"\n🚀 Creating super admin...")
    print(f"   Email: {email}")
    if full_name:
        print(f"   Name: {full_name}")

    asyncio.run(create_super_admin(email, full_name))
    asyncio.run(list_super_admins())


if __name__ == "__main__":
    main()
