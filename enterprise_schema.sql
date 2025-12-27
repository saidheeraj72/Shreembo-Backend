-- ==========================================
-- ENTERPRISE DOCUMENT MANAGEMENT SYSTEM
-- Database Schema with Strict Multi-Tenancy
-- PostgreSQL + Supabase
-- Version: 2.0
-- Date: 2025-12-27
-- ==========================================

-- Enable Required Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";        -- For text search
CREATE EXTENSION IF NOT EXISTS "btree_gin";       -- For multi-column indexes

-- ==========================================
-- ENUMS & TYPES
-- ==========================================

-- Account and Organization Types
CREATE TYPE account_type AS ENUM ('personal', 'organization');
CREATE TYPE plan_type AS ENUM ('free', 'starter', 'professional', 'business', 'enterprise');
CREATE TYPE subscription_status AS ENUM ('active', 'trial', 'expired', 'cancelled', 'past_due');

-- User and Member Statuses
CREATE TYPE user_status AS ENUM ('active', 'inactive', 'suspended', 'deleted');
CREATE TYPE invitation_status AS ENUM ('pending', 'accepted', 'expired', 'cancelled');
CREATE TYPE member_status AS ENUM ('active', 'invited', 'suspended', 'removed');

-- Document and File Types
CREATE TYPE node_type AS ENUM ('file', 'folder');
CREATE TYPE file_status AS ENUM ('active', 'archived', 'deleted', 'processing');
CREATE TYPE version_status AS ENUM ('draft', 'published', 'archived');
CREATE TYPE share_permission AS ENUM ('view', 'comment', 'edit', 'admin');

-- Audit and System Types
CREATE TYPE audit_action AS ENUM (
    'create', 'update', 'delete', 'view', 'download', 'share', 'upload',
    'login', 'logout', 'invite', 'permission_change', 'role_change',
    'archive', 'restore', 'export', 'import'
);
CREATE TYPE log_level AS ENUM ('info', 'warning', 'error', 'critical');

-- Workflow and Approval Types
CREATE TYPE approval_status AS ENUM ('pending', 'approved', 'rejected', 'cancelled');
CREATE TYPE task_status AS ENUM ('pending', 'in_progress', 'completed', 'cancelled');
CREATE TYPE priority_level AS ENUM ('low', 'medium', 'high', 'urgent');

-- ==========================================
-- CORE: TENANCY & ORGANIZATIONS
-- ==========================================

-- Global Super Admins (Platform Level)
CREATE TABLE public.super_admins (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT UNIQUE NOT NULL,
    full_name TEXT,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_login_at TIMESTAMPTZ,
    CONSTRAINT email_format CHECK (email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
);

CREATE INDEX idx_super_admins_email ON public.super_admins(email);
COMMENT ON TABLE public.super_admins IS 'Platform administrators with global access';

-- Organizations (Tenants)
CREATE TABLE public.organizations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    domain TEXT,
    logo_url TEXT,
    description TEXT,

    -- Plan and Limits
    plan_type plan_type DEFAULT 'free',
    subscription_status subscription_status DEFAULT 'trial',
    user_limit INT DEFAULT 5,
    storage_limit_gb BIGINT DEFAULT 10,
    storage_used_gb DECIMAL(10, 2) DEFAULT 0,

    -- Ownership
    owner_id UUID,  -- FK added later

    -- Metadata
    settings JSONB DEFAULT '{}'::jsonb,
    is_active BOOLEAN DEFAULT true,
    trial_ends_at TIMESTAMPTZ,
    subscription_ends_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,

    CONSTRAINT slug_format CHECK (slug ~* '^[a-z0-9-]+$'),
    CONSTRAINT domain_format CHECK (domain IS NULL OR domain ~* '^[a-z0-9.-]+\.[a-z]{2,}$')
);

CREATE INDEX idx_orgs_slug ON public.organizations(slug);
CREATE INDEX idx_orgs_domain ON public.organizations(domain) WHERE domain IS NOT NULL;
CREATE INDEX idx_orgs_owner ON public.organizations(owner_id);
CREATE INDEX idx_orgs_active ON public.organizations(is_active) WHERE is_active = true;

COMMENT ON TABLE public.organizations IS 'Multi-tenant organizations with isolated data';

-- Branches (Departments/Locations)
CREATE TABLE public.branches (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,

    -- Branch Information
    name TEXT NOT NULL,
    code TEXT,
    branch_type TEXT DEFAULT 'office',  -- office, warehouse, store, remote

    -- Location
    address TEXT,
    city TEXT,
    state TEXT,
    country TEXT DEFAULT 'US',
    postal_code TEXT,
    timezone TEXT DEFAULT 'UTC',

    -- Contact
    phone TEXT,
    email TEXT,

    -- Management
    manager_id UUID,  -- FK added later
    parent_branch_id UUID REFERENCES public.branches(id) ON DELETE SET NULL,

    -- Status
    is_active BOOLEAN DEFAULT true,
    settings JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(org_id, code),
    CONSTRAINT email_format CHECK (email IS NULL OR email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
);

CREATE INDEX idx_branches_org ON public.branches(org_id);
CREATE INDEX idx_branches_parent ON public.branches(parent_branch_id);
CREATE INDEX idx_branches_manager ON public.branches(manager_id);
CREATE INDEX idx_branches_active ON public.branches(org_id, is_active);

COMMENT ON TABLE public.branches IS 'Organization branches/departments/locations';

-- ==========================================
-- USERS & AUTHENTICATION
-- ==========================================

-- User Profiles (Links to auth.users)
CREATE TABLE public.profiles (
    id UUID PRIMARY KEY,  -- Same as auth.users.id
    email TEXT NOT NULL,
    full_name TEXT,
    display_name TEXT,
    avatar_url TEXT,
    phone TEXT,

    -- Account Information
    account_type account_type DEFAULT 'personal',
    org_id UUID REFERENCES public.organizations(id) ON DELETE SET NULL,
    primary_branch_id UUID REFERENCES public.branches(id) ON DELETE SET NULL,

    -- Status
    status user_status DEFAULT 'active',
    email_verified BOOLEAN DEFAULT false,
    phone_verified BOOLEAN DEFAULT false,

    -- Security
    two_factor_enabled BOOLEAN DEFAULT false,
    last_login_at TIMESTAMPTZ,
    last_active_at TIMESTAMPTZ,
    password_changed_at TIMESTAMPTZ,

    -- Personal Account Subscription (only for account_type = 'personal')
    plan_type plan_type DEFAULT 'free',
    subscription_status subscription_status DEFAULT 'trial',
    trial_ends_at TIMESTAMPTZ,
    subscription_ends_at TIMESTAMPTZ,

    -- Preferences
    preferences JSONB DEFAULT '{}'::jsonb,
    notification_settings JSONB DEFAULT '{}'::jsonb,

    -- Metadata
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,

    CONSTRAINT email_unique CHECK (email = lower(email))
);

CREATE INDEX idx_profiles_email ON public.profiles(email);
CREATE INDEX idx_profiles_org ON public.profiles(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX idx_profiles_branch ON public.profiles(primary_branch_id);
CREATE INDEX idx_profiles_status ON public.profiles(status);
CREATE INDEX idx_profiles_account_type ON public.profiles(account_type);

COMMENT ON TABLE public.profiles IS 'User profiles linked to Supabase auth.users';

-- Add FK constraints to organizations and branches
ALTER TABLE public.organizations ADD CONSTRAINT fk_org_owner
    FOREIGN KEY (owner_id) REFERENCES public.profiles(id) ON DELETE SET NULL;

ALTER TABLE public.branches ADD CONSTRAINT fk_branch_manager
    FOREIGN KEY (manager_id) REFERENCES public.profiles(id) ON DELETE SET NULL;

-- ==========================================
-- ROLES & PERMISSIONS (RBAC)
-- ==========================================

-- Roles (Per Organization)
CREATE TABLE public.roles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,

    -- Role Information
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    description TEXT,
    color TEXT DEFAULT '#6366f1',
    icon TEXT,

    -- Role Type
    is_system_role BOOLEAN DEFAULT false,  -- Owner, Admin, Member
    is_custom_role BOOLEAN DEFAULT true,
    priority INT DEFAULT 0,  -- Higher number = higher priority

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID REFERENCES public.profiles(id),

    UNIQUE(org_id, slug),
    CONSTRAINT slug_format CHECK (slug ~* '^[a-z0-9_-]+$')
);

CREATE INDEX idx_roles_org ON public.roles(org_id);
CREATE INDEX idx_roles_system ON public.roles(is_system_role);
CREATE INDEX idx_roles_priority ON public.roles(org_id, priority DESC);

COMMENT ON TABLE public.roles IS 'Organization-specific roles for RBAC';

-- Permission Modules
CREATE TABLE public.permission_modules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    icon TEXT,
    category TEXT,  -- core, documents, admin, reports
    sort_order INT DEFAULT 0,
    is_active BOOLEAN DEFAULT true,

    CONSTRAINT key_format CHECK (key ~* '^[a-z0-9_]+$')
);

CREATE INDEX idx_permission_modules_category ON public.permission_modules(category);

COMMENT ON TABLE public.permission_modules IS 'Permission modules (e.g., documents, users, audit)';

-- Permissions (Actions within Modules)
CREATE TABLE public.permissions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    module_id UUID NOT NULL REFERENCES public.permission_modules(id) ON DELETE CASCADE,

    -- Permission Details
    action TEXT NOT NULL,  -- view, create, edit, delete, share, etc.
    name TEXT NOT NULL,
    description TEXT,

    -- Permission Level
    is_dangerous BOOLEAN DEFAULT false,  -- Requires confirmation
    requires_2fa BOOLEAN DEFAULT false,

    -- Metadata
    sort_order INT DEFAULT 0,

    UNIQUE(module_id, action),
    CONSTRAINT action_format CHECK (action ~* '^[a-z0-9_]+$')
);

CREATE INDEX idx_permissions_module ON public.permissions(module_id);
CREATE INDEX idx_permissions_dangerous ON public.permissions(is_dangerous);

COMMENT ON TABLE public.permissions IS 'Granular permissions for each module';

-- Role-Permission Mapping
CREATE TABLE public.role_permissions (
    role_id UUID NOT NULL REFERENCES public.roles(id) ON DELETE CASCADE,
    permission_id UUID NOT NULL REFERENCES public.permissions(id) ON DELETE CASCADE,
    granted_at TIMESTAMPTZ DEFAULT NOW(),
    granted_by UUID REFERENCES public.profiles(id),

    PRIMARY KEY (role_id, permission_id)
);

CREATE INDEX idx_role_perms_role ON public.role_permissions(role_id);
CREATE INDEX idx_role_perms_permission ON public.role_permissions(permission_id);

COMMENT ON TABLE public.role_permissions IS 'Maps permissions to roles';

-- User-Permission Overrides (Direct User Permissions)
CREATE TABLE public.user_permissions (
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    permission_id UUID NOT NULL REFERENCES public.permissions(id) ON DELETE CASCADE,
    is_granted BOOLEAN DEFAULT true,  -- false = explicitly denied
    granted_at TIMESTAMPTZ DEFAULT NOW(),
    granted_by UUID REFERENCES public.profiles(id),
    expires_at TIMESTAMPTZ,  -- Temporary permissions

    PRIMARY KEY (user_id, permission_id)
);

CREATE INDEX idx_user_perms_user ON public.user_permissions(user_id);
CREATE INDEX idx_user_perms_permission ON public.user_permissions(permission_id);
CREATE INDEX idx_user_perms_expires ON public.user_permissions(expires_at) WHERE expires_at IS NOT NULL;

COMMENT ON TABLE public.user_permissions IS 'User-level permission overrides (grants or denials)';

-- ==========================================
-- ORGANIZATION MEMBERSHIP
-- ==========================================

-- Organization Members
CREATE TABLE public.organization_members (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    role_id UUID REFERENCES public.roles(id) ON DELETE SET NULL,

    -- Membership Details
    status member_status DEFAULT 'active',
    title TEXT,  -- Job title
    department TEXT,
    employee_id TEXT,

    -- Dates
    joined_at TIMESTAMPTZ DEFAULT NOW(),
    invited_at TIMESTAMPTZ,
    invited_by UUID REFERENCES public.profiles(id),
    removed_at TIMESTAMPTZ,
    removed_by UUID REFERENCES public.profiles(id),

    -- Metadata
    notes TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,

    UNIQUE(org_id, user_id)
);

CREATE INDEX idx_org_members_org ON public.organization_members(org_id);
CREATE INDEX idx_org_members_user ON public.organization_members(user_id);
CREATE INDEX idx_org_members_role ON public.organization_members(role_id);
CREATE INDEX idx_org_members_status ON public.organization_members(org_id, status);

COMMENT ON TABLE public.organization_members IS 'User membership in organizations';

-- User-Branch Assignments (Many-to-Many)
CREATE TABLE public.user_branches (
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    branch_id UUID NOT NULL REFERENCES public.branches(id) ON DELETE CASCADE,
    is_primary BOOLEAN DEFAULT false,
    assigned_at TIMESTAMPTZ DEFAULT NOW(),
    assigned_by UUID REFERENCES public.profiles(id),

    PRIMARY KEY (user_id, branch_id)
);

CREATE INDEX idx_user_branches_user ON public.user_branches(user_id);
CREATE INDEX idx_user_branches_branch ON public.user_branches(branch_id);
CREATE INDEX idx_user_branches_primary ON public.user_branches(user_id, is_primary) WHERE is_primary = true;

COMMENT ON TABLE public.user_branches IS 'User assignments to branches';

-- ==========================================
-- INVITATIONS
-- ==========================================

-- Organization Invitations
CREATE TABLE public.organization_invitations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,

    -- Invitation Details
    email TEXT NOT NULL,
    role_id UUID REFERENCES public.roles(id) ON DELETE SET NULL,
    branch_id UUID REFERENCES public.branches(id) ON DELETE SET NULL,

    -- Token and Status
    invite_token TEXT UNIQUE NOT NULL DEFAULT encode(gen_random_bytes(32), 'hex'),
    status invitation_status DEFAULT 'pending',

    -- Invitation Metadata
    message TEXT,
    invited_by UUID NOT NULL REFERENCES public.profiles(id),
    invited_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '7 days',

    -- Acceptance
    accepted_at TIMESTAMPTZ,
    accepted_by UUID REFERENCES public.profiles(id),

    -- Metadata
    metadata JSONB DEFAULT '{}'::jsonb,

    CONSTRAINT email_format CHECK (email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
);

CREATE INDEX idx_invitations_org ON public.organization_invitations(org_id);
CREATE INDEX idx_invitations_email ON public.organization_invitations(email);
CREATE INDEX idx_invitations_token ON public.organization_invitations(invite_token);
CREATE INDEX idx_invitations_status ON public.organization_invitations(status);
CREATE INDEX idx_invitations_expires ON public.organization_invitations(expires_at);

COMMENT ON TABLE public.organization_invitations IS 'Pending invitations to join organizations';

-- Organization Requests (For new orgs)
CREATE TABLE public.organization_requests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL,
    user_email TEXT NOT NULL,
    user_full_name TEXT NOT NULL,

    -- Organization Details
    org_name TEXT NOT NULL,
    org_domain TEXT,
    org_size TEXT,  -- 1-10, 11-50, 51-200, 201-500, 500+
    industry TEXT,

    -- Request Status
    status approval_status DEFAULT 'pending',
    rejection_reason TEXT,

    -- Review
    reviewed_at TIMESTAMPTZ,
    reviewed_by UUID REFERENCES public.super_admins(id),

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_org_requests_status ON public.organization_requests(status);
CREATE INDEX idx_org_requests_user ON public.organization_requests(user_id);

COMMENT ON TABLE public.organization_requests IS 'Requests to create new organizations';

-- ==========================================
-- GROUPS (User Collections)
-- ==========================================

-- Groups
CREATE TABLE public.groups (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,

    -- Group Information
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    description TEXT,
    color TEXT DEFAULT '#6366f1',
    icon TEXT,

    -- Group Type
    group_type TEXT DEFAULT 'custom',  -- custom, department, project, team

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID REFERENCES public.profiles(id),

    UNIQUE(org_id, slug)
);

CREATE INDEX idx_groups_org ON public.groups(org_id);
CREATE INDEX idx_groups_type ON public.groups(group_type);

COMMENT ON TABLE public.groups IS 'User groups for organization and permissions';

-- Group Members
CREATE TABLE public.group_members (
    group_id UUID NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    is_admin BOOLEAN DEFAULT false,
    joined_at TIMESTAMPTZ DEFAULT NOW(),
    added_by UUID REFERENCES public.profiles(id),

    PRIMARY KEY (group_id, user_id)
);

CREATE INDEX idx_group_members_group ON public.group_members(group_id);
CREATE INDEX idx_group_members_user ON public.group_members(user_id);

COMMENT ON TABLE public.group_members IS 'Users belonging to groups';

-- ==========================================
-- DOCUMENT STORAGE
-- ==========================================

-- Storage Nodes (Files & Folders)
CREATE TABLE public.storage_nodes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID REFERENCES public.organizations(id) ON DELETE CASCADE,  -- NULL for personal
    parent_id UUID REFERENCES public.storage_nodes(id) ON DELETE CASCADE,

    -- Node Information
    name TEXT NOT NULL,
    node_type node_type NOT NULL,

    -- File Metadata (for files)
    storage_path TEXT,  -- Path in Supabase Storage
    file_size BIGINT,
    mime_type TEXT,
    file_extension TEXT,
    checksum TEXT,  -- For duplicate detection

    -- Document Properties
    description TEXT,
    tags TEXT[],

    -- Ownership
    owner_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    branch_id UUID REFERENCES public.branches(id) ON DELETE SET NULL,

    -- Status
    status file_status DEFAULT 'active',
    is_public BOOLEAN DEFAULT false,
    is_starred BOOLEAN DEFAULT false,

    -- Versioning
    version_number INT DEFAULT 1,
    is_latest_version BOOLEAN DEFAULT true,

    -- Metadata
    metadata JSONB DEFAULT '{}'::jsonb,
    custom_fields JSONB DEFAULT '{}'::jsonb,

    -- Dates
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,

    -- Full-text search
    search_vector TSVECTOR,

    CONSTRAINT folder_no_file_data CHECK (
        node_type = 'file' OR (
            storage_path IS NULL AND
            file_size IS NULL AND
            mime_type IS NULL
        )
    )
);

CREATE INDEX idx_nodes_org ON public.storage_nodes(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX idx_nodes_parent ON public.storage_nodes(parent_id);
CREATE INDEX idx_nodes_owner ON public.storage_nodes(owner_id);
CREATE INDEX idx_nodes_branch ON public.storage_nodes(branch_id);
CREATE INDEX idx_nodes_type ON public.storage_nodes(node_type);
CREATE INDEX idx_nodes_status ON public.storage_nodes(status);
CREATE INDEX idx_nodes_tags ON public.storage_nodes USING GIN(tags);
CREATE INDEX idx_nodes_search ON public.storage_nodes USING GIN(search_vector);
CREATE INDEX idx_nodes_checksum ON public.storage_nodes(checksum) WHERE checksum IS NOT NULL;

COMMENT ON TABLE public.storage_nodes IS 'Hierarchical file and folder storage';

-- File Versions
CREATE TABLE public.file_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    node_id UUID NOT NULL REFERENCES public.storage_nodes(id) ON DELETE CASCADE,

    -- Version Information
    version_number INT NOT NULL,
    storage_path TEXT NOT NULL,
    file_size BIGINT NOT NULL,
    checksum TEXT,

    -- Version Details
    status version_status DEFAULT 'published',
    change_summary TEXT,

    -- Ownership
    created_by UUID NOT NULL REFERENCES public.profiles(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(node_id, version_number)
);

CREATE INDEX idx_versions_node ON public.file_versions(node_id);
CREATE INDEX idx_versions_created ON public.file_versions(created_at DESC);

COMMENT ON TABLE public.file_versions IS 'Version history for files';

-- Node Permissions (Share & Access Control)
CREATE TABLE public.node_permissions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    node_id UUID NOT NULL REFERENCES public.storage_nodes(id) ON DELETE CASCADE,

    -- Target (User or Group)
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    group_id UUID REFERENCES public.groups(id) ON DELETE CASCADE,

    -- Permission Level
    permission share_permission NOT NULL DEFAULT 'view',

    -- Sharing Details
    granted_by UUID NOT NULL REFERENCES public.profiles(id),
    granted_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ,

    -- Access Tracking
    last_accessed_at TIMESTAMPTZ,
    access_count INT DEFAULT 0,

    CONSTRAINT single_target CHECK (
        (user_id IS NOT NULL AND group_id IS NULL) OR
        (user_id IS NULL AND group_id IS NOT NULL)
    )
);

CREATE INDEX idx_node_perms_node ON public.node_permissions(node_id);
CREATE INDEX idx_node_perms_user ON public.node_permissions(user_id) WHERE user_id IS NOT NULL;
CREATE INDEX idx_node_perms_group ON public.node_permissions(group_id) WHERE group_id IS NOT NULL;
CREATE INDEX idx_node_perms_expires ON public.node_permissions(expires_at) WHERE expires_at IS NOT NULL;

COMMENT ON TABLE public.node_permissions IS 'File and folder sharing permissions';

-- Node-Branch Associations (Multi-branch file access)
CREATE TABLE public.node_branches (
    node_id UUID NOT NULL REFERENCES public.storage_nodes(id) ON DELETE CASCADE,
    branch_id UUID NOT NULL REFERENCES public.branches(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (node_id, branch_id)
);

CREATE INDEX idx_node_branches_node ON public.node_branches(node_id);
CREATE INDEX idx_node_branches_branch ON public.node_branches(branch_id);

COMMENT ON TABLE public.node_branches IS 'Multi-branch file associations';

-- ==========================================
-- DOCUMENT WORKFLOW & COLLABORATION
-- ==========================================

-- File Comments
CREATE TABLE public.file_comments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    node_id UUID NOT NULL REFERENCES public.storage_nodes(id) ON DELETE CASCADE,
    parent_comment_id UUID REFERENCES public.file_comments(id) ON DELETE CASCADE,

    -- Comment Content
    content TEXT NOT NULL,
    mentioned_users UUID[],

    -- Metadata
    created_by UUID NOT NULL REFERENCES public.profiles(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    is_resolved BOOLEAN DEFAULT false,
    resolved_at TIMESTAMPTZ,
    resolved_by UUID REFERENCES public.profiles(id)
);

CREATE INDEX idx_comments_node ON public.file_comments(node_id);
CREATE INDEX idx_comments_parent ON public.file_comments(parent_comment_id);
CREATE INDEX idx_comments_user ON public.file_comments(created_by);
CREATE INDEX idx_comments_unresolved ON public.file_comments(node_id, is_resolved) WHERE is_resolved = false;

COMMENT ON TABLE public.file_comments IS 'Comments and discussions on files';

-- Document Approvals
CREATE TABLE public.document_approvals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    node_id UUID NOT NULL REFERENCES public.storage_nodes(id) ON DELETE CASCADE,

    -- Approval Details
    title TEXT NOT NULL,
    description TEXT,
    required_approvers UUID[],
    optional_approvers UUID[],

    -- Status
    status approval_status DEFAULT 'pending',
    due_date TIMESTAMPTZ,

    -- Workflow
    created_by UUID NOT NULL REFERENCES public.profiles(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,

    -- Metadata
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_approvals_node ON public.document_approvals(node_id);
CREATE INDEX idx_approvals_status ON public.document_approvals(status);
CREATE INDEX idx_approvals_due ON public.document_approvals(due_date) WHERE due_date IS NOT NULL;

COMMENT ON TABLE public.document_approvals IS 'Document approval workflows';

-- Approval Responses
CREATE TABLE public.approval_responses (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    approval_id UUID NOT NULL REFERENCES public.document_approvals(id) ON DELETE CASCADE,
    approver_id UUID NOT NULL REFERENCES public.profiles(id),

    -- Response
    status approval_status NOT NULL,
    comments TEXT,
    responded_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(approval_id, approver_id)
);

CREATE INDEX idx_approval_responses_approval ON public.approval_responses(approval_id);
CREATE INDEX idx_approval_responses_approver ON public.approval_responses(approver_id);

COMMENT ON TABLE public.approval_responses IS 'Individual approval responses';

-- ==========================================
-- ACTIVITY & AUDIT LOGS
-- ==========================================

-- Audit Logs
CREATE TABLE public.audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID REFERENCES public.organizations(id) ON DELETE CASCADE,

    -- Actor
    user_id UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    user_email TEXT,
    user_name TEXT,

    -- Action
    action audit_action NOT NULL,
    resource_type TEXT NOT NULL,  -- e.g., 'file', 'user', 'role'
    resource_id UUID,
    resource_name TEXT,

    -- Details
    description TEXT,
    details JSONB DEFAULT '{}'::jsonb,
    changes JSONB,  -- Before/after for updates

    -- Context
    ip_address INET,
    user_agent TEXT,
    location TEXT,

    -- Metadata
    severity log_level DEFAULT 'info',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_org ON public.audit_logs(org_id, created_at DESC);
CREATE INDEX idx_audit_user ON public.audit_logs(user_id, created_at DESC);
CREATE INDEX idx_audit_resource ON public.audit_logs(resource_type, resource_id);
CREATE INDEX idx_audit_action ON public.audit_logs(action);
CREATE INDEX idx_audit_created ON public.audit_logs(created_at DESC);
CREATE INDEX idx_audit_severity ON public.audit_logs(severity) WHERE severity IN ('error', 'critical');

COMMENT ON TABLE public.audit_logs IS 'Comprehensive audit trail for all system actions';

-- User Activity Tracking
CREATE TABLE public.user_activity (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,

    -- Activity Details
    activity_type TEXT NOT NULL,  -- login, file_view, file_download, etc.
    resource_id UUID,
    resource_type TEXT,

    -- Context
    ip_address INET,
    user_agent TEXT,
    duration_seconds INT,

    -- Metadata
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_activity_user ON public.user_activity(user_id, created_at DESC);
CREATE INDEX idx_activity_type ON public.user_activity(activity_type);
CREATE INDEX idx_activity_resource ON public.user_activity(resource_type, resource_id);

COMMENT ON TABLE public.user_activity IS 'User activity tracking for analytics';

-- ==========================================
-- NOTIFICATIONS
-- ==========================================

-- Notifications
CREATE TABLE public.notifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,

    -- Notification Content
    type TEXT NOT NULL,  -- mention, comment, share, approval_request, etc.
    title TEXT NOT NULL,
    message TEXT,
    icon TEXT,

    -- Link
    link_url TEXT,
    resource_id UUID,
    resource_type TEXT,

    -- Status
    is_read BOOLEAN DEFAULT false,
    is_archived BOOLEAN DEFAULT false,
    read_at TIMESTAMPTZ,

    -- Metadata
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);

CREATE INDEX idx_notifications_user ON public.notifications(user_id, created_at DESC);
CREATE INDEX idx_notifications_unread ON public.notifications(user_id, is_read) WHERE is_read = false;
CREATE INDEX idx_notifications_type ON public.notifications(type);

COMMENT ON TABLE public.notifications IS 'In-app notifications for users';

-- ==========================================
-- ANALYTICS & REPORTING
-- ==========================================

-- Storage Usage by Organization
CREATE MATERIALIZED VIEW public.org_storage_usage AS
SELECT
    o.id AS org_id,
    o.name AS org_name,
    COUNT(DISTINCT sn.id) AS total_files,
    COALESCE(SUM(sn.file_size), 0) AS storage_used_bytes,
    ROUND((COALESCE(SUM(sn.file_size), 0)::DECIMAL / (1024^3))::numeric, 2) AS storage_used_gb,
    o.storage_limit_gb,
    ROUND(((COALESCE(SUM(sn.file_size), 0)::DECIMAL / (1024^3) / o.storage_limit_gb) * 100)::numeric, 2) AS usage_percentage,
    MAX(sn.created_at) AS last_upload_at
FROM public.organizations o
LEFT JOIN public.storage_nodes sn ON o.id = sn.org_id AND sn.node_type = 'file' AND sn.status = 'active'
GROUP BY o.id, o.name, o.storage_limit_gb;

CREATE UNIQUE INDEX idx_org_storage_usage ON public.org_storage_usage(org_id);

-- User Activity Summary
CREATE MATERIALIZED VIEW public.user_activity_summary AS
SELECT
    p.id AS user_id,
    p.full_name,
    p.email,
    p.org_id,
    COUNT(DISTINCT ua.id) AS total_activities,
    MAX(ua.created_at) AS last_activity_at,
    COUNT(DISTINCT sn.id) FILTER (WHERE sn.owner_id = p.id) AS files_owned,
    COUNT(DISTINCT fc.id) FILTER (WHERE fc.created_by = p.id) AS comments_made
FROM public.profiles p
LEFT JOIN public.user_activity ua ON p.id = ua.user_id
LEFT JOIN public.storage_nodes sn ON p.id = sn.owner_id AND sn.status = 'active'
LEFT JOIN public.file_comments fc ON p.id = fc.created_by
GROUP BY p.id, p.full_name, p.email, p.org_id;

CREATE UNIQUE INDEX idx_user_activity_summary ON public.user_activity_summary(user_id);

-- ==========================================
-- HELPER FUNCTIONS
-- ==========================================

-- Update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Update search vector for full-text search
CREATE OR REPLACE FUNCTION update_search_vector()
RETURNS TRIGGER AS $$
BEGIN
    NEW.search_vector := to_tsvector('english',
        COALESCE(NEW.name, '') || ' ' ||
        COALESCE(NEW.description, '') || ' ' ||
        COALESCE(array_to_string(NEW.tags, ' '), '')
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Auto-expire invitations
CREATE OR REPLACE FUNCTION expire_invitations()
RETURNS void AS $$
BEGIN
    UPDATE public.organization_invitations
    SET status = 'expired'
    WHERE status = 'pending'
    AND expires_at < NOW();
END;
$$ LANGUAGE plpgsql;

-- Update organization storage usage
CREATE OR REPLACE FUNCTION update_org_storage()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
        UPDATE public.organizations
        SET storage_used_gb = (
            SELECT COALESCE(SUM(file_size), 0)::DECIMAL / (1024^3)
            FROM public.storage_nodes
            WHERE org_id = NEW.org_id
            AND node_type = 'file'
            AND status = 'active'
        )
        WHERE id = NEW.org_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ==========================================
-- TRIGGERS
-- ==========================================

-- Auto-update timestamps
CREATE TRIGGER update_organizations_updated_at
    BEFORE UPDATE ON public.organizations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_branches_updated_at
    BEFORE UPDATE ON public.branches
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_profiles_updated_at
    BEFORE UPDATE ON public.profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_storage_nodes_updated_at
    BEFORE UPDATE ON public.storage_nodes
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_roles_updated_at
    BEFORE UPDATE ON public.roles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_groups_updated_at
    BEFORE UPDATE ON public.groups
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Update search vector on storage_nodes
CREATE TRIGGER update_storage_nodes_search
    BEFORE INSERT OR UPDATE OF name, description, tags ON public.storage_nodes
    FOR EACH ROW EXECUTE FUNCTION update_search_vector();

-- Update org storage on file changes
CREATE TRIGGER update_org_storage_on_insert
    AFTER INSERT ON public.storage_nodes
    FOR EACH ROW
    WHEN (NEW.node_type = 'file' AND NEW.org_id IS NOT NULL)
    EXECUTE FUNCTION update_org_storage();

CREATE TRIGGER update_org_storage_on_update
    AFTER UPDATE OF file_size, status ON public.storage_nodes
    FOR EACH ROW
    WHEN (NEW.node_type = 'file' AND NEW.org_id IS NOT NULL)
    EXECUTE FUNCTION update_org_storage();

-- ==========================================
-- ROW LEVEL SECURITY (RLS)
-- ==========================================

-- Enable RLS on all tables
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.branches ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.permission_modules ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organization_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organization_invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.storage_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.file_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.node_permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.file_comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;

-- Profiles: Users can view/update their own profile
CREATE POLICY "Users can view own profile" ON public.profiles
    FOR SELECT USING (auth.uid() = id);

CREATE POLICY "Users can update own profile" ON public.profiles
    FOR UPDATE USING (auth.uid() = id);

-- Organizations: Members can view their org
CREATE POLICY "Members can view their organization" ON public.organizations
    FOR SELECT USING (
        id IN (
            SELECT org_id FROM public.organization_members
            WHERE user_id = auth.uid() AND status = 'active'
        )
    );

-- Storage Nodes: Complex permissions based on ownership, sharing, and org membership
CREATE POLICY "Users can view accessible files" ON public.storage_nodes
    FOR SELECT USING (
        owner_id = auth.uid() OR  -- Own files
        is_public = true OR  -- Public files
        org_id IN (  -- Org files
            SELECT org_id FROM public.organization_members
            WHERE user_id = auth.uid() AND status = 'active'
        ) OR
        id IN (  -- Shared files
            SELECT node_id FROM public.node_permissions
            WHERE user_id = auth.uid()
        )
    );

-- Audit Logs: Users can view logs from their org
CREATE POLICY "Users can view org audit logs" ON public.audit_logs
    FOR SELECT USING (
        org_id IN (
            SELECT org_id FROM public.organization_members
            WHERE user_id = auth.uid() AND status = 'active'
        )
    );

-- Notifications: Users can view their own notifications
CREATE POLICY "Users can view own notifications" ON public.notifications
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Users can update own notifications" ON public.notifications
    FOR UPDATE USING (user_id = auth.uid());

-- ==========================================
-- SEED DATA
-- ==========================================

-- Insert Permission Modules
INSERT INTO public.permission_modules (key, name, description, category, sort_order) VALUES
('documents', 'Documents', 'File and folder management', 'core', 1),
('branches', 'Branches', 'Branch and location management', 'core', 2),
('users', 'Users', 'User and member management', 'admin', 3),
('roles', 'Roles & Permissions', 'Role-based access control', 'admin', 4),
('groups', 'Groups', 'User group management', 'admin', 5),
('audit', 'Audit Logs', 'Activity and audit trail', 'admin', 6),
('settings', 'Settings', 'Organization settings', 'admin', 7),
('reports', 'Reports', 'Analytics and reporting', 'admin', 8),
('approvals', 'Approvals', 'Document approval workflows', 'core', 9),
('storage', 'Storage', 'Storage management', 'admin', 10)
ON CONFLICT (key) DO NOTHING;

-- Insert Permissions for each module
WITH module_ids AS (
    SELECT id, key FROM public.permission_modules
)
INSERT INTO public.permissions (module_id, action, name, description, is_dangerous, sort_order) VALUES
-- Documents Module
((SELECT id FROM module_ids WHERE key = 'documents'), 'view', 'View Documents', 'View files and folders', false, 1),
((SELECT id FROM module_ids WHERE key = 'documents'), 'create', 'Create Documents', 'Upload and create new files', false, 2),
((SELECT id FROM module_ids WHERE key = 'documents'), 'edit', 'Edit Documents', 'Modify existing files', false, 3),
((SELECT id FROM module_ids WHERE key = 'documents'), 'delete', 'Delete Documents', 'Delete files and folders', true, 4),
((SELECT id FROM module_ids WHERE key = 'documents'), 'share', 'Share Documents', 'Share files with others', false, 5),
((SELECT id FROM module_ids WHERE key = 'documents'), 'download', 'Download Documents', 'Download files', false, 6),
((SELECT id FROM module_ids WHERE key = 'documents'), 'version', 'Manage Versions', 'Create and manage file versions', false, 7),

-- Branches Module
((SELECT id FROM module_ids WHERE key = 'branches'), 'view', 'View Branches', 'View branch list', false, 1),
((SELECT id FROM module_ids WHERE key = 'branches'), 'create', 'Create Branches', 'Create new branches', false, 2),
((SELECT id FROM module_ids WHERE key = 'branches'), 'edit', 'Edit Branches', 'Modify branch details', false, 3),
((SELECT id FROM module_ids WHERE key = 'branches'), 'delete', 'Delete Branches', 'Delete branches', true, 4),
((SELECT id FROM module_ids WHERE key = 'branches'), 'assign_users', 'Assign Users', 'Assign users to branches', false, 5),

-- Users Module
((SELECT id FROM module_ids WHERE key = 'users'), 'view', 'View Users', 'View user list', false, 1),
((SELECT id FROM module_ids WHERE key = 'users'), 'invite', 'Invite Users', 'Send user invitations', false, 2),
((SELECT id FROM module_ids WHERE key = 'users'), 'edit', 'Edit Users', 'Modify user details', false, 3),
((SELECT id FROM module_ids WHERE key = 'users'), 'remove', 'Remove Users', 'Remove users from organization', true, 4),
((SELECT id FROM module_ids WHERE key = 'users'), 'assign_roles', 'Assign Roles', 'Assign roles to users', false, 5),
((SELECT id FROM module_ids WHERE key = 'users'), 'manage_permissions', 'Manage User Permissions', 'Grant/revoke individual permissions', false, 6),

-- Roles Module
((SELECT id FROM module_ids WHERE key = 'roles'), 'view', 'View Roles', 'View role list', false, 1),
((SELECT id FROM module_ids WHERE key = 'roles'), 'create', 'Create Roles', 'Create custom roles', false, 2),
((SELECT id FROM module_ids WHERE key = 'roles'), 'edit', 'Edit Roles', 'Modify role permissions', false, 3),
((SELECT id FROM module_ids WHERE key = 'roles'), 'delete', 'Delete Roles', 'Delete custom roles', true, 4),

-- Groups Module
((SELECT id FROM module_ids WHERE key = 'groups'), 'view', 'View Groups', 'View group list', false, 1),
((SELECT id FROM module_ids WHERE key = 'groups'), 'create', 'Create Groups', 'Create new groups', false, 2),
((SELECT id FROM module_ids WHERE key = 'groups'), 'edit', 'Edit Groups', 'Modify group details', false, 3),
((SELECT id FROM module_ids WHERE key = 'groups'), 'delete', 'Delete Groups', 'Delete groups', true, 4),
((SELECT id FROM module_ids WHERE key = 'groups'), 'manage_members', 'Manage Members', 'Add/remove group members', false, 5),

-- Audit Module
((SELECT id FROM module_ids WHERE key = 'audit'), 'view', 'View Audit Logs', 'View audit trail', false, 1),
((SELECT id FROM module_ids WHERE key = 'audit'), 'export', 'Export Audit Logs', 'Export audit data', false, 2),

-- Settings Module
((SELECT id FROM module_ids WHERE key = 'settings'), 'view', 'View Settings', 'View organization settings', false, 1),
((SELECT id FROM module_ids WHERE key = 'settings'), 'edit', 'Edit Settings', 'Modify organization settings', true, 2),

-- Reports Module
((SELECT id FROM module_ids WHERE key = 'reports'), 'view', 'View Reports', 'Access analytics and reports', false, 1),
((SELECT id FROM module_ids WHERE key = 'reports'), 'export', 'Export Reports', 'Export report data', false, 2),

-- Approvals Module
((SELECT id FROM module_ids WHERE key = 'approvals'), 'view', 'View Approvals', 'View approval requests', false, 1),
((SELECT id FROM module_ids WHERE key = 'approvals'), 'create', 'Create Approvals', 'Create approval workflows', false, 2),
((SELECT id FROM module_ids WHERE key = 'approvals'), 'approve', 'Approve Documents', 'Approve or reject documents', false, 3),

-- Storage Module
((SELECT id FROM module_ids WHERE key = 'storage'), 'view', 'View Storage Usage', 'View storage statistics', false, 1),
((SELECT id FROM module_ids WHERE key = 'storage'), 'manage', 'Manage Storage', 'Configure storage settings', true, 2)

ON CONFLICT (module_id, action) DO NOTHING;

-- ==========================================
-- REFRESH MATERIALIZED VIEWS (Run periodically)
-- ==========================================

-- Uncomment to run manually or set up pg_cron
-- REFRESH MATERIALIZED VIEW CONCURRENTLY public.org_storage_usage;
-- REFRESH MATERIALIZED VIEW CONCURRENTLY public.user_activity_summary;

-- ==========================================
-- END OF SCHEMA
-- ==========================================
