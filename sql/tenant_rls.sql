-- Run once as the table owner. The agent role must not be superuser and must not have BYPASSRLS.
-- Existing rows get tenant_id 'local'. The API sets app.tenant_id per request before SELECT.

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL DEFAULT 'local';
ALTER TABLE public.vehicles ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL DEFAULT 'local';
ALTER TABLE public.rides ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL DEFAULT 'local';
ALTER TABLE public.payments ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL DEFAULT 'local';
ALTER TABLE public.ratings ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL DEFAULT 'local';

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.users FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.users;
CREATE POLICY tenant_isolation ON public.users
  USING (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

ALTER TABLE public.vehicles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.vehicles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.vehicles;
CREATE POLICY tenant_isolation ON public.vehicles
  USING (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

ALTER TABLE public.rides ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rides FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.rides;
CREATE POLICY tenant_isolation ON public.rides
  USING (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

ALTER TABLE public.payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.payments FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.payments;
CREATE POLICY tenant_isolation ON public.payments
  USING (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

ALTER TABLE public.ratings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ratings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.ratings;
CREATE POLICY tenant_isolation ON public.ratings
  USING (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
