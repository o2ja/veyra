"""identity and tenancy: users, sessions, tokens, recovery codes, rate-limit counters, tenants,
settings, memberships, invitations; RLS; SECURITY DEFINER lookups owned by rebate_lookup

Revision ID: 20261109_2130_identity_tenancy
Revises: 20261109_1200_baseline
"""

from alembic import op

from migrations._rls import enable_tenant_rls

revision = "20261109_2130_identity_tenancy"
down_revision = "20261109_1200_baseline"
branch_labels = None
depends_on = None

ROLES = "('admin', 'approver', 'analyst', 'viewer')"


def upgrade() -> None:
    # ---- global tables (no RLS; DATABASE_SCHEMA.md §2) -------------------------------------------
    op.execute(
        """
        CREATE TABLE users (
            id uuid NOT NULL,
            email citext NOT NULL,
            password_hash text NOT NULL,
            full_name text NOT NULL,
            email_verified_at timestamptz,
            mfa_enabled boolean NOT NULL DEFAULT false,
            mfa_secret_enc bytea,
            mfa_last_used_step bigint,
            status text NOT NULL DEFAULT 'active',
            failed_login_count integer NOT NULL DEFAULT 0,
            lockout_level integer NOT NULL DEFAULT 0,
            locked_until timestamptz,
            last_login_at timestamptz,
            password_changed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_users PRIMARY KEY (id),
            CONSTRAINT uq_users_email UNIQUE (email),
            CONSTRAINT ck_users_status CHECK (status IN ('active', 'disabled')),
            CONSTRAINT ck_users_email_length CHECK (char_length(email) BETWEEN 3 AND 254),
            CONSTRAINT ck_users_email_ascii CHECK (email::text ~ '^[!-~]+@[!-~]+$'),
            CONSTRAINT ck_users_full_name_length CHECK (char_length(full_name) BETWEEN 1 AND 200),
            CONSTRAINT ck_users_mfa_secret CHECK (NOT mfa_enabled OR mfa_secret_enc IS NOT NULL),
            CONSTRAINT ck_users_counters CHECK (failed_login_count >= 0 AND lockout_level >= 0)
        )
        """
    )

    # ---- tenancy -----------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE tenants (
            id uuid NOT NULL,
            name text NOT NULL,
            slug text NOT NULL,
            status text NOT NULL DEFAULT 'active',
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_tenants PRIMARY KEY (id),
            CONSTRAINT uq_tenants_slug UNIQUE (slug),
            CONSTRAINT ck_tenants_status CHECK (status IN ('active', 'suspended', 'closed')),
            CONSTRAINT ck_tenants_name_length CHECK (char_length(name) BETWEEN 1 AND 200),
            CONSTRAINT ck_tenants_slug_format CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$')
        )
        """
    )
    enable_tenant_rls("tenants", column="id")

    op.execute(
        """
        CREATE TABLE tenant_settings (
            tenant_id uuid NOT NULL,
            business_timezone text NOT NULL DEFAULT 'America/Chicago',
            currency_code char(3) NOT NULL DEFAULT 'USD',
            mfa_required boolean NOT NULL DEFAULT true,
            four_eyes_required boolean NOT NULL DEFAULT true,
            ai_processing_enabled boolean NOT NULL DEFAULT false,
            retention_years integer NOT NULL DEFAULT 7,
            default_claim_rounding jsonb,
            revision integer NOT NULL DEFAULT 1,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_tenant_settings PRIMARY KEY (tenant_id),
            CONSTRAINT fk_tenant_settings_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT ck_tenant_settings_currency CHECK (currency_code = 'USD'),
            CONSTRAINT ck_tenant_settings_retention CHECK (retention_years BETWEEN 1 AND 30),
            CONSTRAINT ck_tenant_settings_timezone_length CHECK (char_length(business_timezone) <= 64)
        )
        """
    )
    enable_tenant_rls("tenant_settings")

    op.execute(
        f"""
        CREATE TABLE memberships (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            role text NOT NULL,
            status text NOT NULL DEFAULT 'active',
            invited_by uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_memberships PRIMARY KEY (id),
            CONSTRAINT uq_memberships_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_memberships_tenant_id_user_id UNIQUE (tenant_id, user_id),
            CONSTRAINT fk_memberships_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_memberships_users FOREIGN KEY (user_id) REFERENCES users (id),
            CONSTRAINT fk_memberships_invited_by FOREIGN KEY (invited_by) REFERENCES users (id),
            CONSTRAINT ck_memberships_role CHECK (role IN {ROLES}),
            CONSTRAINT ck_memberships_status CHECK (status IN ('active', 'disabled'))
        )
        """
    )
    op.execute("CREATE INDEX ix_memberships_user_id ON memberships (user_id)")
    enable_tenant_rls("memberships")

    op.execute(
        f"""
        CREATE TABLE invitations (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            email citext NOT NULL,
            role text NOT NULL,
            token_hash text NOT NULL,
            invited_by uuid NOT NULL,
            expires_at timestamptz NOT NULL,
            accepted_at timestamptz,
            accepted_user_id uuid,
            revoked_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_invitations PRIMARY KEY (id),
            CONSTRAINT uq_invitations_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_invitations_token_hash UNIQUE (token_hash),
            CONSTRAINT fk_invitations_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
            CONSTRAINT fk_invitations_invited_by FOREIGN KEY (invited_by) REFERENCES users (id),
            CONSTRAINT fk_invitations_accepted_user FOREIGN KEY (accepted_user_id) REFERENCES users (id),
            CONSTRAINT ck_invitations_role CHECK (role IN {ROLES}),
            CONSTRAINT ck_invitations_single_outcome CHECK (accepted_at IS NULL OR revoked_at IS NULL)
        )
        """
    )
    op.execute("CREATE INDEX ix_invitations_tenant_id_email ON invitations (tenant_id, email)")
    enable_tenant_rls("invitations")

    # ---- remaining global identity tables ------------------------------------------------------
    op.execute(
        """
        CREATE TABLE sessions (
            id text NOT NULL,
            user_id uuid NOT NULL,
            active_tenant_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            last_seen_at timestamptz NOT NULL DEFAULT now(),
            idle_expires_at timestamptz NOT NULL,
            absolute_expires_at timestamptz NOT NULL,
            mfa_verified boolean NOT NULL DEFAULT false,
            reauthenticated_at timestamptz,
            mfa_failed_count integer NOT NULL DEFAULT 0,
            ip inet,
            user_agent text,
            revoked_at timestamptz,
            CONSTRAINT pk_sessions PRIMARY KEY (id),
            CONSTRAINT fk_sessions_users FOREIGN KEY (user_id) REFERENCES users (id),
            CONSTRAINT fk_sessions_tenants FOREIGN KEY (active_tenant_id) REFERENCES tenants (id),
            CONSTRAINT ck_sessions_id_format CHECK (id ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_sessions_user_agent_length CHECK (char_length(user_agent) <= 512)
        )
        """
    )
    op.execute("CREATE INDEX ix_sessions_user_id ON sessions (user_id)")

    op.execute(
        """
        CREATE TABLE user_tokens (
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            purpose text NOT NULL,
            token_hash text NOT NULL,
            new_email citext,
            expires_at timestamptz NOT NULL,
            used_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_user_tokens PRIMARY KEY (id),
            CONSTRAINT uq_user_tokens_token_hash UNIQUE (token_hash),
            CONSTRAINT fk_user_tokens_users FOREIGN KEY (user_id) REFERENCES users (id),
            CONSTRAINT ck_user_tokens_purpose
                CHECK (purpose IN ('email_verify', 'password_reset', 'email_change')),
            CONSTRAINT ck_user_tokens_new_email
                CHECK ((purpose = 'email_change') = (new_email IS NOT NULL))
        )
        """
    )
    op.execute("CREATE INDEX ix_user_tokens_user_id_purpose ON user_tokens (user_id, purpose)")

    op.execute(
        """
        CREATE TABLE mfa_recovery_codes (
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            code_hash text NOT NULL,
            used_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_mfa_recovery_codes PRIMARY KEY (id),
            CONSTRAINT uq_mfa_recovery_codes_code_hash UNIQUE (code_hash),
            CONSTRAINT fk_mfa_recovery_codes_users FOREIGN KEY (user_id) REFERENCES users (id)
        )
        """
    )
    op.execute("CREATE INDEX ix_mfa_recovery_codes_user_id ON mfa_recovery_codes (user_id)")

    op.execute(
        """
        CREATE TABLE rate_limit_counters (
            key text NOT NULL,
            window_start timestamptz NOT NULL,
            count integer NOT NULL,
            expires_at timestamptz NOT NULL,
            CONSTRAINT pk_rate_limit_counters PRIMARY KEY (key),
            CONSTRAINT ck_rate_limit_counters_count CHECK (count >= 0),
            CONSTRAINT ck_rate_limit_counters_key_length CHECK (char_length(key) <= 200)
        )
        """
    )
    op.execute("CREATE INDEX ix_rate_limit_counters_expires_at ON rate_limit_counters (expires_at)")

    # Registration is held here until the email is verified: no users/tenants row exists before
    # then, so nobody can pre-register someone else's address (S1 kickoff review finding 1).
    op.execute(
        """
        CREATE TABLE pending_registrations (
            id uuid NOT NULL,
            email citext NOT NULL,
            password_hash text NOT NULL,
            full_name text NOT NULL,
            workspace_name text NOT NULL,
            token_hash text NOT NULL,
            expires_at timestamptz NOT NULL,
            used_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_pending_registrations PRIMARY KEY (id),
            CONSTRAINT uq_pending_registrations_token_hash UNIQUE (token_hash),
            CONSTRAINT ck_pending_registrations_full_name_length
                CHECK (char_length(full_name) BETWEEN 1 AND 200),
            CONSTRAINT ck_pending_registrations_workspace_name_length
                CHECK (char_length(workspace_name) BETWEEN 1 AND 200)
        )
        """
    )
    op.execute("CREATE INDEX ix_pending_registrations_email ON pending_registrations (email)")

    # ---- sanctioned cross-tenant lookups (DATABASE_SCHEMA.md §2 correction) --------------------
    # FORCE RLS applies to the table owner too, so these functions are owned by rebate_lookup
    # (NOLOGIN, BYPASSRLS; created by init-db.sh) and expose only the columns the caller needs.
    op.execute("GRANT SELECT ON tenants, memberships, invitations TO rebate_lookup")
    op.execute("GRANT USAGE ON SCHEMA public TO rebate_lookup")
    op.execute(
        """
        CREATE FUNCTION fn_user_memberships(p_user_id uuid)
        RETURNS TABLE (tenant_id uuid, tenant_name text, role text, status text)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
            SELECT m.tenant_id, t.name, m.role, m.status
            FROM public.memberships m
            JOIN public.tenants t ON t.id = m.tenant_id
            WHERE m.user_id = p_user_id AND t.status = 'active'
            ORDER BY t.name, m.tenant_id
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION fn_invitation_by_token(p_token_hash text)
        RETURNS TABLE (tenant_id uuid, invitation_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
            SELECT i.tenant_id, i.id FROM public.invitations i WHERE i.token_hash = p_token_hash
        $$
        """
    )
    # ALTER ... OWNER needs the new owner to hold CREATE on the schema; grant it only for the
    # duration of the ownership change so rebate_lookup can never create objects itself.
    # Grants are set while rebate_owner still owns the functions; ALTER OWNER carries the ACL over.
    # rebate_owner keeps EXECUTE on fn_user_memberships for the operator runbook
    # (infra/runbooks/locked-out-admin.md); readonly and ai_writer get nothing.
    op.execute("GRANT CREATE ON SCHEMA public TO rebate_lookup")
    for fn, grantees in (
        ("fn_user_memberships(uuid)", "rebate_app, rebate_owner"),
        ("fn_invitation_by_token(text)", "rebate_app"),
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO {grantees}")
        op.execute(f"ALTER FUNCTION {fn} OWNER TO rebate_lookup")
    op.execute("REVOKE CREATE ON SCHEMA public FROM rebate_lookup")

    # Default privileges give rebate_readonly SELECT on every table; the global credential tables
    # are not tenant-scoped and hold secrets, so support/debug access must not reach them.
    op.execute(
        "REVOKE ALL ON users, sessions, user_tokens, mfa_recovery_codes, rate_limit_counters, "
        "pending_registrations FROM rebate_readonly"
    )
