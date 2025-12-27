ALTER TABLE public.branches 
ADD COLUMN IF NOT EXISTS branch_code text,
ADD COLUMN IF NOT EXISTS branch_type text,
ADD COLUMN IF NOT EXISTS address text,
ADD COLUMN IF NOT EXISTS city text,
ADD COLUMN IF NOT EXISTS state text,
ADD COLUMN IF NOT EXISTS country text,
ADD COLUMN IF NOT EXISTS pincode text,
ADD COLUMN IF NOT EXISTS phone text,
ADD COLUMN IF NOT EXISTS email text,
ADD COLUMN IF NOT EXISTS manager_name text,
ADD COLUMN IF NOT EXISTS status text DEFAULT 'active';
