-- ==========================================
-- RESET (DROP EVERYTHING WITH CASCADE)
-- ==========================================
drop trigger if exists on_auth_user_created on auth.users cascade;
drop function if exists public.handle_new_user() cascade;
drop function if exists get_my_org_id() cascade;
drop function if exists get_my_account_type() cascade;
drop function if exists has_global_permission(text) cascade;
drop function if exists has_node_permission(uuid, permission_level) cascade;

-- Drop RLS Policies first as they depend on functions
alter table profiles disable row level security;
alter table organizations disable row level security;
alter table storage_nodes disable row level security;
alter table chat_sessions disable row level security;
alter table chat_messages disable row level security;
alter table organization_requests disable row level security;
alter table roles disable row level security;
alter table app_modules disable row level security;
alter table app_permissions disable row level security;
alter table role_permissions disable row level security;
alter table node_permissions disable row level security;
alter table user_permissions disable row level security;

-- Drop tables with CASCADE to ensure all constraints and dependent objects are removed
drop table if exists public.audit_logs cascade;
drop table if exists public.message_citations cascade;
drop table if exists public.chat_messages cascade;
drop table if exists public.chat_sessions cascade;
drop table if exists public.node_permissions cascade;
drop table if exists public.node_branches cascade;
drop table if exists public.storage_nodes cascade;
drop table if exists public.user_branches cascade;
drop table if exists public.group_members cascade;
drop table if exists public.groups cascade;
drop table if exists public.user_permissions cascade;
drop table if exists public.user_roles cascade;
drop table if exists public.role_permissions cascade;
drop table if exists public.app_permissions cascade;
drop table if exists public.app_modules cascade;
drop table if exists public.roles cascade;
drop table if exists public.branches cascade;
drop table if exists public.organizations cascade;
drop table if exists public.organization_requests cascade;
drop table if exists public.super_admins cascade;
drop table if exists public.profiles cascade;

-- Drop types with CASCADE
drop type if exists account_type cascade;
drop type if exists plan_type cascade;
drop type if exists chat_role cascade;
drop type if exists permission_level cascade;
drop type if exists node_type cascade;
drop type if exists user_status cascade;
drop type if exists request_status cascade;

-- Enable UUID extension
create extension if not exists "uuid-ossp";

-- ==========================================
-- 1. ENUMS & TYPES
-- ==========================================
create type request_status as enum ('pending', 'approved', 'rejected');
create type user_status as enum ('active', 'inactive', 'suspended');
create type node_type as enum ('file', 'folder');
create type permission_level as enum ('view', 'edit', 'admin');
create type chat_role as enum ('user', 'assistant', 'system');
create type plan_type as enum ('personal', 'team', 'enterprise');
create type account_type as enum ('personal', 'organization');

-- ==========================================
-- 2. SUPER ADMIN & REQUESTS
-- ==========================================

create table public.super_admins (
  id uuid primary key default uuid_generate_v4(),
  email text unique not null,
  created_at timestamptz default now()
);

create table public.organization_requests (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid not null, -- Removed FK to auth.users to decouple slightly, or keep if using Supabase Auth
  user_email text not null,
  user_full_name text not null,
  org_name text not null,
  status request_status default 'pending',
  rejection_reason text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- ==========================================
-- 3. ORGANIZATION CORE
-- ==========================================

create table public.organizations (
  id uuid primary key default uuid_generate_v4(),
  name text not null,
  slug text unique not null,
  domain text,
  plan_type plan_type default 'personal',
  user_limit int default 1,
  storage_limit_mb bigint default 100,
  owner_id uuid, -- Link to profile
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table public.branches (
  id uuid primary key default uuid_generate_v4(),
  org_id uuid references public.organizations(id) on delete cascade not null,
  name text not null,
  location text,
  created_at timestamptz default now()
);

-- ==========================================
-- 4. USER MANAGEMENT
-- ==========================================

create table public.profiles (
  id uuid primary key, -- Maps to auth.users.id manually via backend
  email text not null,
  full_name text,
  avatar_url text,
  org_id uuid references public.organizations(id) on delete set null,
  status user_status default 'active',
  account_type account_type not null default 'personal',
  subscription_expiry timestamptz,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

alter table public.organizations 
  add constraint fk_organizations_owner 
  foreign key (owner_id) references public.profiles(id);

create table public.roles (
  id uuid primary key default uuid_generate_v4(),
  org_id uuid references public.organizations(id) on delete cascade not null,
  name text not null,
  description text,
  color text default 'primary',
  is_system_role boolean default false,
  created_at timestamptz default now()
);

create table public.app_modules (
  id uuid primary key default uuid_generate_v4(),
  key text unique not null,
  name text not null
);

create table public.app_permissions (
  id uuid primary key default uuid_generate_v4(),
  module_id uuid references public.app_modules(id) on delete cascade not null,
  action text not null,
  description text,
  unique (module_id, action)
);

create table public.role_permissions (
  role_id uuid references public.roles(id) on delete cascade not null,
  permission_id uuid references public.app_permissions(id) on delete cascade not null,
  primary key (role_id, permission_id)
);

create table public.user_roles (
  user_id uuid references public.profiles(id) on delete cascade not null,
  role_id uuid references public.roles(id) on delete cascade not null,
  primary key (user_id, role_id)
);

create table public.user_permissions (
  user_id uuid references public.profiles(id) on delete cascade not null,
  permission_id uuid references public.app_permissions(id) on delete cascade not null,
  primary key (user_id, permission_id)
);

create table public.groups (
  id uuid primary key default uuid_generate_v4(),
  org_id uuid references public.organizations(id) on delete cascade not null,
  name text not null,
  description text,
  created_at timestamptz default now()
);

create table public.organization_members (
  id uuid primary key default uuid_generate_v4(),
  org_id uuid references public.organizations(id) on delete cascade not null,
  user_id uuid references public.profiles(id) on delete cascade not null,
  role_id uuid references public.roles(id) on delete set null,
  status text default 'active', -- active, invited, suspended
  joined_at timestamptz default now(),
  unique(org_id, user_id)
);

create table public.group_members (
  group_id uuid references public.groups(id) on delete cascade not null,
  user_id uuid references public.profiles(id) on delete cascade not null,
  joined_at timestamptz default now(),
  primary key (group_id, user_id)
);

create table public.user_branches (
  user_id uuid references public.profiles(id) on delete cascade not null,
  branch_id uuid references public.branches(id) on delete cascade not null,
  is_primary boolean default false,
  primary key (user_id, branch_id)
);

-- ==========================================
-- 5. DOCUMENT REPOSITORY
-- ==========================================

create table public.storage_nodes (
  id uuid primary key default uuid_generate_v4(),
  org_id uuid references public.organizations(id) on delete cascade, -- Nullable for personal files?
  parent_id uuid references public.storage_nodes(id) on delete cascade, 
  name text not null,
  node_type node_type not null,
  storage_path text,
  file_size bigint,
  mime_type text,
  owner_id uuid references public.profiles(id) on delete set null,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table public.node_branches (
  node_id uuid references public.storage_nodes(id) on delete cascade not null,
  branch_id uuid references public.branches(id) on delete cascade not null,
  primary key (node_id, branch_id)
);

create table public.node_permissions (
  id uuid primary key default uuid_generate_v4(),
  node_id uuid references public.storage_nodes(id) on delete cascade not null,
  user_id uuid references public.profiles(id) on delete cascade,
  group_id uuid references public.groups(id) on delete cascade,
  permission permission_level not null default 'view',
  granted_by uuid references public.profiles(id),
  created_at timestamptz default now(),
  constraint check_target check (
    (user_id is not null and group_id is null) or 
    (user_id is null and group_id is not null)
  )
);

-- ==========================================
-- 6. CHAT INTELLIGENCE
-- ==========================================

create table public.chat_sessions (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid references public.profiles(id) on delete cascade not null,
  org_id uuid references public.organizations(id) on delete cascade, -- Nullable for personal chats
  title text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table public.chat_messages (
  id uuid primary key default uuid_generate_v4(),
  session_id uuid references public.chat_sessions(id) on delete cascade not null,
  role chat_role not null,
  content text not null,
  metadata jsonb,
  created_at timestamptz default now()
);

create table public.message_citations (
  message_id uuid references public.chat_messages(id) on delete cascade not null,
  node_id uuid references public.storage_nodes(id) on delete cascade not null,
  relevance_score float,
  citation_text text,
  primary key (message_id, node_id)
);

-- ==========================================
-- 7. AUDIT LOGS
-- ==========================================

create table public.audit_logs (
  id uuid primary key default uuid_generate_v4(),
  org_id uuid references public.organizations(id) on delete cascade,
  user_id uuid references public.profiles(id) on delete set null,
  action text not null,
  resource_table text not null,
  resource_id uuid,
  details jsonb,
  ip_address inet,
  user_agent text,
  created_at timestamptz default now()
);

-- ==========================================
-- 8. ROW LEVEL SECURITY (RLS) - SIMPLIFIED
-- ==========================================
-- We enable RLS but give full access to authenticated users by default for now
-- The BACKEND (FastAPI) will handle the strict logic.
-- If you want the frontend to query Supabase directly, we'd need policies here.
-- For a backend-heavy approach, we can leave RLS enabled but open, or use the Service Role key in FastAPI.

alter table profiles enable row level security;
alter table organizations enable row level security;
alter table storage_nodes enable row level security;
alter table chat_sessions enable row level security;
alter table chat_messages enable row level security;
alter table organization_requests enable row level security;
alter table roles enable row level security;
alter table app_modules enable row level security;
alter table app_permissions enable row level security;
alter table role_permissions enable row level security;
alter table node_permissions enable row level security;
alter table user_permissions enable row level security;

-- Basic "View Own" policies (just in case frontend queries directly)
create policy "Users view their own profile" on profiles
  for select using (auth.uid() = id);

create policy "Users update their own profile" on profiles
  for update using (auth.uid() = id);
  
-- ==========================================
-- 9. SEED DATA (Modules and Permissions)
-- ==========================================

insert into public.app_modules (key, name) values
  ('documents', 'Documents'),
  ('branches', 'Branches'),
  ('users', 'Users'),
  ('audit', 'Audit'),
  ('settings', 'Settings'),
  ('subscription', 'Subscription')
on conflict do nothing;

-- Get app_module IDs and insert app_permissions
with module_ids as (
  select id, key from public.app_modules
)
insert into public.app_permissions (module_id, action, description)
values
  -- Documents Module Permissions
  ((select id from module_ids where key = 'documents'), 'view', 'View documents'),
  ((select id from module_ids where key = 'documents'), 'create', 'Create new documents'),
  ((select id from module_ids where key = 'documents'), 'edit', 'Edit existing documents'),
  ((select id from module_ids where key = 'documents'), 'delete', 'Delete documents'),
  ((select id from module_ids where key = 'documents'), 'share', 'Share documents'),

  -- Branches Module Permissions
  ((select id from module_ids where key = 'branches'), 'view', 'View branches'),
  ((select id from module_ids where key = 'branches'), 'create', 'Create new branches'),
  ((select id from module_ids where key = 'branches'), 'edit', 'Edit existing branches'),
  ((select id from module_ids where key = 'branches'), 'delete', 'Delete branches'),
  ((select id from module_ids where key = 'branches'), 'manage', 'Manage branch settings'),

  -- Users Module Permissions
  ((select id from module_ids where key = 'users'), 'view', 'View users'),
  ((select id from module_ids where key = 'users'), 'create', 'Invite/Create new users'),
  ((select id from module_ids where key = 'users'), 'edit', 'Edit user profiles/roles'),
  ((select id from module_ids where key = 'users'), 'delete', 'Remove users'),
  ((select id from module_ids where key = 'users'), 'assign', 'Assign roles to users'),

  -- Audit Module Permissions
  ((select id from module_ids where key = 'audit'), 'view', 'View audit logs'),
  ((select id from module_ids where key = 'audit'), 'export', 'Export audit logs'),

  -- Settings Module Permissions
  ((select id from module_ids where key = 'settings'), 'view', 'View organization settings'),
  ((select id from module_ids where key = 'settings'), 'edit', 'Edit organization settings'),

  -- Subscription Module Permissions
  ((select id from module_ids where key = 'subscription'), 'view', 'View subscription details'),
  ((select id from module_ids where key = 'subscription'), 'edit', 'Manage subscription')
on conflict (module_id, action) do nothing;
