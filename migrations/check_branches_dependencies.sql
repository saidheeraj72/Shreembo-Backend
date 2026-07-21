-- READ-ONLY diagnostic. Run in the Supabase SQL editor BEFORE
-- convert_branches_to_units.sql to confirm what depends on the branches table
-- and its columns. Nothing here modifies data. Review each result set.

-- 1. How many rows are at stake.
SELECT count(*) AS branch_row_count FROM public.branches;

-- 2. Current columns (confirm which legacy office columns still exist).
SELECT ordinal_position, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'branches'
ORDER BY ordinal_position;

-- 3. Constraints ON branches (CHECK / UNIQUE / FK / PK).
--    Dropping a column auto-drops constraints that involve ONLY that column
--    (e.g. email_format on email). Anything spanning other columns would need
--    explicit handling.
SELECT conname, contype, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'public.branches'::regclass
ORDER BY contype, conname;

-- 4. Indexes ON branches (idx_branches_parent is dropped with parent_branch_id).
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public' AND tablename = 'branches'
ORDER BY indexname;

-- 5. RLS policies on branches (in case any reference a dropped column).
SELECT policyname, cmd, qual, with_check
FROM pg_policies
WHERE schemaname = 'public' AND tablename = 'branches';

-- 6. AFFILIATED TABLES: every foreign key that points AT branches.
--    Expected: profiles.primary_branch_id, user_branches.branch_id,
--    organization_invitations.branch_id, storage_nodes.branch_id,
--    node_branches.branch_id — all referencing branches(id), NOT the office
--    columns, so the column drops do not affect them.
SELECT
    conrelid::regclass AS referencing_table,
    conname            AS fk_name,
    pg_get_constraintdef(oid) AS definition,
    confdeltype        AS on_delete   -- a=no action, r=restrict, c=cascade, n=set null, d=set default
FROM pg_constraint
WHERE confrelid = 'public.branches'::regclass
ORDER BY referencing_table;

-- 7. Safety assertion: any FK (in the whole DB) that references one of the
--    columns being dropped. This MUST return zero rows for the drop to be safe.
SELECT
    conrelid::regclass AS referencing_table,
    conname            AS fk_name,
    pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE confrelid = 'public.branches'::regclass
  AND EXISTS (
      SELECT 1
      FROM unnest(confkey) AS ref_attnum
      JOIN pg_attribute a
        ON a.attrelid = 'public.branches'::regclass
       AND a.attnum = ref_attnum
      WHERE a.attname IN (
          'branch_type','address','city','state','country',
          'postal_code','timezone','phone','email','parent_branch_id'
      )
  );
