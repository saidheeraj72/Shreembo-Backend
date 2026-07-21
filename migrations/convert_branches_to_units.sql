-- Convert the `branches` table from the legacy office/department format to the
-- unit (vessel) format used by the app. The frontend relabels "branch" as
-- "unit"; the table name and /admin/branches API path are unchanged.
-- Apply manually in the Supabase SQL editor (this repo has no migration runner).

-- 1. Add vessel columns.
ALTER TABLE public.branches
    ADD COLUMN IF NOT EXISTS unit_type TEXT,          -- 'Vessel' | 'Office'
    ADD COLUMN IF NOT EXISTS flag TEXT,               -- flag state
    ADD COLUMN IF NOT EXISTS imo_number TEXT,         -- 7-digit IMO number
    ADD COLUMN IF NOT EXISTS mmsi TEXT,               -- 9-digit MMSI
    ADD COLUMN IF NOT EXISTS call_sign TEXT,
    ADD COLUMN IF NOT EXISTS vessel_type TEXT,        -- e.g. Bulk Carrier, Oil Tanker
    ADD COLUMN IF NOT EXISTS port_of_registry TEXT,
    ADD COLUMN IF NOT EXISTS year_built TEXT;         -- 4-digit year

-- 2. Drop the legacy office/location columns. Dropping each column also drops
--    the objects that depend solely on it — the email_format CHECK constraint
--    (email) and the self-referential FK + idx_branches_parent index
--    (parent_branch_id).
ALTER TABLE public.branches
    DROP COLUMN IF EXISTS branch_type,
    DROP COLUMN IF EXISTS address,
    DROP COLUMN IF EXISTS city,
    DROP COLUMN IF EXISTS state,
    DROP COLUMN IF EXISTS country,
    DROP COLUMN IF EXISTS postal_code,
    DROP COLUMN IF EXISTS timezone,
    DROP COLUMN IF EXISTS phone,
    DROP COLUMN IF EXISTS email,
    DROP COLUMN IF EXISTS parent_branch_id;

COMMENT ON TABLE public.branches IS 'Organization units (vessels); API path /admin/branches';
