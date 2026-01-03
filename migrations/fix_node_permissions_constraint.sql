-- Add unique constraints to node_permissions table to support upserts

-- First, clean up any duplicates (keep the most recent one)
DELETE FROM node_permissions a USING node_permissions b
WHERE a.id < b.id
AND a.node_id = b.node_id
AND a.user_id = b.user_id
AND a.user_id IS NOT NULL;

DELETE FROM node_permissions a USING node_permissions b
WHERE a.id < b.id
AND a.node_id = b.node_id
AND a.group_id = b.group_id
AND a.group_id IS NOT NULL;

-- Add unique index for user permissions
CREATE UNIQUE INDEX IF NOT EXISTS idx_node_permissions_user_unique 
ON node_permissions(node_id, user_id) 
WHERE user_id IS NOT NULL;

-- Add unique index for group permissions
CREATE UNIQUE INDEX IF NOT EXISTS idx_node_permissions_group_unique 
ON node_permissions(node_id, group_id) 
WHERE group_id IS NOT NULL;

-- Add explicit unique constraint for ON CONFLICT support (user_id)
-- Note: ON CONFLICT needs a constraint name or index inference. 
-- For complex partial indexes, it's safer to use the index name or ADD CONSTRAINT with exclusion,
-- but standard ON CONFLICT (col1, col2) requires a UNIQUE constraint or index covering those columns.
-- Since user_id is nullable, a standard UNIQUE(node_id, user_id) allows multiple (node_id, NULL).
-- However, our app logic strictly separates user vs group permissions.

-- The error "there is no unique or exclusion constraint matching the ON CONFLICT specification"
-- specifically looks for a unique constraint or index on the columns provided in ON CONFLICT.
-- Our code uses `on_conflict="node_id,user_id"`.

-- IMPORTANT: Postgres allows multiple NULLs in a UNIQUE constraint.
-- So UNIQUE(node_id, user_id) is valid and will enforce uniqueness for (node_id, <some_user_uuid>).
-- It will NOT enforce uniqueness for (node_id, NULL), which is fine for us because that case is covered by group_id.

ALTER TABLE node_permissions
ADD CONSTRAINT node_permissions_user_unique UNIQUE (node_id, user_id);
