-- Migration: 20260918123000_claim_guest_case_rpc.sql
-- Description: Atomic, race-safe RPC function to claim a temporary guest case to an authenticated Supabase user.

CREATE OR REPLACE FUNCTION claim_guest_case(
    p_case_id UUID,
    p_guest_session_id UUID,
    p_user_id UUID
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_case RECORD;
    v_session RECORD;
    v_now TIMESTAMPTZ := now();
BEGIN
    -- 1. Input sanitization & validation
    IF p_case_id IS NULL OR p_guest_session_id IS NULL OR p_user_id IS NULL THEN
        RETURN jsonb_build_object('code', 'INVALID_ARGUMENTS');
    END IF;

    -- 2. Verify guest session exists and is unexpired inside the atomic DB operation
    SELECT id, expires_at
    INTO v_session
    FROM guest_sessions
    WHERE id = p_guest_session_id;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('code', 'GUEST_SESSION_NOT_FOUND');
    END IF;

    IF v_session.expires_at <= v_now THEN
        RETURN jsonb_build_object('code', 'GUEST_SESSION_EXPIRED');
    END IF;

    -- 3. Acquire exclusive row-level lock on target case to prevent race conditions
    SELECT id, user_id, guest_session_id, retention_type, expires_at
    INTO v_case
    FROM cases
    WHERE id = p_case_id
    FOR UPDATE;

    -- 4. Case existence check
    IF NOT FOUND THEN
        RETURN jsonb_build_object('code', 'CASE_NOT_FOUND');
    END IF;

    -- 5. Deterministic check for already claimed cases
    IF v_case.user_id IS NOT NULL THEN
        IF v_case.user_id = p_user_id THEN
            RETURN jsonb_build_object('code', 'ALREADY_CLAIMED_BY_SELF');
        ELSE
            RETURN jsonb_build_object('code', 'ALREADY_CLAIMED_BY_OTHER');
        END IF;
    END IF;

    -- 6. Verify case currently belongs to the supplied guest session
    IF v_case.guest_session_id IS NULL OR v_case.guest_session_id != p_guest_session_id THEN
        RETURN jsonb_build_object('code', 'GUEST_OWNERSHIP_MISMATCH');
    END IF;

    -- 7. Atomically transfer case ownership and set persistent retention
    UPDATE cases
    SET user_id = p_user_id,
        guest_session_id = NULL,
        retention_type = 'persistent',
        expires_at = NULL,
        updated_at = v_now
    WHERE id = p_case_id;

    -- 8. Atomically update all child documents belonging to this case to persistent retention
    UPDATE documents
    SET retention_type = 'persistent',
        expires_at = NULL,
        updated_at = v_now
    WHERE case_id = p_case_id;

    -- 9. Return structured success with transaction timestamp
    RETURN jsonb_build_object(
        'code', 'OK',
        'case_id', p_case_id,
        'user_id', p_user_id,
        'claimed_at', v_now,
        'retention_type', 'persistent'
    );
END;
$$;

-- Secure execution permissions: strictly accessible only by backend service_role
REVOKE ALL ON FUNCTION claim_guest_case(UUID, UUID, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION claim_guest_case(UUID, UUID, UUID) FROM anon;
REVOKE ALL ON FUNCTION claim_guest_case(UUID, UUID, UUID) FROM authenticated;
GRANT EXECUTE ON FUNCTION claim_guest_case(UUID, UUID, UUID) TO service_role;
