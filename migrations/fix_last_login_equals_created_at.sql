-- Fix users where last_login was incorrectly set to created_at
-- These users never actually logged in since signup, so set last_login to NULL
-- The lifecycle system uses COALESCE(last_login, created_at) so NULL is handled correctly

-- First, see how many users are affected
-- SELECT COUNT(*) FROM users WHERE last_login = created_at;

-- Update users where last_login equals created_at (never logged in)
UPDATE users
SET last_login = NULL
WHERE last_login = created_at;

-- Verify the fix
-- SELECT COUNT(*) FROM users WHERE last_login IS NULL;
