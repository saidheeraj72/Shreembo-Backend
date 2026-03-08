"""Admin role-management entry points."""

from .service import AdminService

list_roles = AdminService.list_roles
get_role_with_permissions = AdminService.get_role_with_permissions
create_role = AdminService.create_role
update_role = AdminService.update_role
delete_role = AdminService.delete_role
get_all_permissions = AdminService.get_all_permissions
