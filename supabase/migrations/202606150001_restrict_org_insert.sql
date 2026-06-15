-- Org creation is service-role/admin-only until self-service onboarding has
-- proper validation (Spec B). The authenticated-user INSERT policy opened a
-- privilege-escalation path: any signed-in user could POST an organizations row
-- via PostgREST, triggering the auto-owner membership and gaining full
-- dashboard/API access to deployment-level Snowflake data. Service-role inserts
-- bypass RLS entirely, so the trigger still fires correctly for operator-seeded
-- orgs (see docs/auth-and-deployment.md, "First-user bootstrap").
drop policy if exists organizations_insert_for_authenticated on organizations;
