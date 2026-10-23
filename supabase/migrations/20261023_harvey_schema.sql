-- ==============================================================================
-- Harvey Outbound Sales Engine — Comprehensive PostgreSQL Schema for Supabase
-- Project: imwouckhxvqeispzdgfr
-- Mirrors all Harvey tables with full types, constraints, and indexes.
-- ==============================================================================

-- 1. Companies
CREATE TABLE IF NOT EXISTS public.companies (
    id TEXT PRIMARY KEY,
    name TEXT DEFAULT '',
    domain TEXT DEFAULT '',
    website TEXT DEFAULT '',
    description TEXT DEFAULT '',
    industry TEXT DEFAULT '',
    company_size TEXT DEFAULT '',
    location TEXT DEFAULT '',
    source TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    external_id TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    tech_stack_json JSONB DEFAULT '[]'::jsonb,
    signals_json JSONB DEFAULT '[]'::jsonb,
    prospects_checked_at TIMESTAMPTZ,
    profile_checked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_companies_domain ON public.companies(domain);
CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_domain ON public.companies(domain) WHERE domain != '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_external ON public.companies(external_id) WHERE external_id != '';

-- 2. Prospects
CREATE TABLE IF NOT EXISTS public.prospects (
    id TEXT PRIMARY KEY,
    company_id TEXT DEFAULT '',
    first_name TEXT DEFAULT '',
    last_name TEXT DEFAULT '',
    email TEXT DEFAULT '',
    email_verified INTEGER DEFAULT 0,
    email_status TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    phone_verified INTEGER DEFAULT 0,
    linkedin_url TEXT DEFAULT '',
    title TEXT DEFAULT '',
    seniority TEXT DEFAULT '',
    department TEXT DEFAULT '',
    source TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    status TEXT DEFAULT 'new',
    score INTEGER DEFAULT 0,
    personalization_notes TEXT DEFAULT '',
    company TEXT DEFAULT '',
    industry TEXT DEFAULT '',
    company_size TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_prospects_company_id ON public.prospects(company_id);
CREATE INDEX IF NOT EXISTS idx_prospects_status ON public.prospects(status);
CREATE INDEX IF NOT EXISTS idx_prospects_email ON public.prospects(email);
CREATE UNIQUE INDEX IF NOT EXISTS uq_prospects_email ON public.prospects(email) WHERE email != '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_prospects_linkedin ON public.prospects(linkedin_url) WHERE linkedin_url != '';

-- 3. Outbox (Human-in-the-loop review ladder & send queue)
CREATE TABLE IF NOT EXISTS public.outbox (
    id TEXT PRIMARY KEY,
    campaign_id TEXT DEFAULT '',
    prospect_id TEXT DEFAULT '',
    conversation_id TEXT DEFAULT '',
    step INTEGER DEFAULT 1,
    kind TEXT DEFAULT 'sequence',
    to_email TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    body TEXT DEFAULT '',
    status TEXT DEFAULT 'pending_review',
    send_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    provider TEXT DEFAULT '',
    message_id TEXT DEFAULT '',
    thread_ref TEXT DEFAULT '',
    in_reply_to TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now())
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_outbox_campaign_step
    ON public.outbox(campaign_id, prospect_id, step)
    WHERE campaign_id != '' AND kind = 'sequence';
CREATE INDEX IF NOT EXISTS idx_outbox_status_send_at ON public.outbox(status, send_at);
CREATE INDEX IF NOT EXISTS idx_outbox_prospect ON public.outbox(prospect_id);

-- 4. Campaigns
CREATE TABLE IF NOT EXISTS public.campaigns (
    id TEXT PRIMARY KEY,
    name TEXT DEFAULT '',
    channel TEXT DEFAULT 'email',
    instantly_campaign_id TEXT DEFAULT '',
    sequence_json JSONB DEFAULT '[]'::jsonb,
    prospect_ids_json JSONB DEFAULT '[]'::jsonb,
    status TEXT DEFAULT 'draft',
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_campaigns_status ON public.campaigns(status);

-- 5. Signal Codes (Governed Buying Signal Vocabulary)
CREATE TABLE IF NOT EXISTS public.signal_codes (
    code TEXT PRIMARY KEY,
    label TEXT DEFAULT '',
    description TEXT DEFAULT '',
    category TEXT DEFAULT '',
    value_type TEXT DEFAULT 'text',
    collector TEXT DEFAULT '',
    cost_note TEXT DEFAULT '',
    status TEXT DEFAULT 'proposed',
    confidence_floor NUMERIC DEFAULT 0.0,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now())
);

-- 6. Observations (Fact discovery time series)
CREATE TABLE IF NOT EXISTS public.observations (
    id TEXT PRIMARY KEY,
    company_id TEXT DEFAULT '',
    prospect_id TEXT DEFAULT '',
    signal_code TEXT NOT NULL,
    collector TEXT DEFAULT '',
    value_num NUMERIC,
    value_text TEXT DEFAULT '',
    confidence NUMERIC DEFAULT 1.0,
    evidence_url TEXT DEFAULT '',
    observed_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()),
    run_id TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_obs_company ON public.observations(company_id);
CREATE INDEX IF NOT EXISTS idx_obs_signal ON public.observations(signal_code);
CREATE INDEX IF NOT EXISTS idx_obs_run ON public.observations(run_id);

-- 7. Runs (Audit log of collectors and agent cycles)
CREATE TABLE IF NOT EXISTS public.runs (
    id TEXT PRIMARY KEY,
    stage TEXT DEFAULT '',
    status TEXT DEFAULT 'running',
    provider TEXT DEFAULT '',
    records INTEGER DEFAULT 0,
    cost_usd NUMERIC DEFAULT 0.0,
    params_json JSONB DEFAULT '{}'::jsonb,
    error TEXT DEFAULT '',
    started_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()),
    ended_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_runs_stage_started ON public.runs(stage, started_at);

-- 8. Conversations (Outreach threads & reply classification)
CREATE TABLE IF NOT EXISTS public.conversations (
    id TEXT PRIMARY KEY,
    prospect_id TEXT DEFAULT '',
    campaign_id TEXT DEFAULT '',
    channel TEXT DEFAULT 'email',
    thread_json JSONB DEFAULT '[]'::jsonb,
    intent TEXT DEFAULT '',
    stage TEXT DEFAULT 'initial_outreach',
    status TEXT DEFAULT 'open',
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_conversations_status ON public.conversations(status);
CREATE INDEX IF NOT EXISTS idx_conversations_campaign_id ON public.conversations(campaign_id);
CREATE INDEX IF NOT EXISTS idx_conversations_prospect_id ON public.conversations(prospect_id);

-- 9. Usage Events (Token, LLM cost & attribution tracking)
CREATE TABLE IF NOT EXISTS public.usage_events (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()),
    agent TEXT DEFAULT '',
    task TEXT DEFAULT '',
    session_id TEXT DEFAULT '',
    request_key TEXT DEFAULT '',
    model TEXT DEFAULT '',
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0,
    cost_usd NUMERIC DEFAULT 0.0,
    duration_ms INTEGER DEFAULT 0,
    num_turns INTEGER DEFAULT 0,
    is_error INTEGER DEFAULT 0,
    source TEXT DEFAULT 'result_json',
    provider TEXT DEFAULT 'claude'
);

CREATE INDEX IF NOT EXISTS idx_usage_events_created ON public.usage_events(created_at);
CREATE INDEX IF NOT EXISTS idx_usage_events_agent ON public.usage_events(agent);

-- 10. Settings (Operational flags and kill switches)
CREATE TABLE IF NOT EXISTS public.settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT '',
    updated_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now())
);

-- ==============================================================================
-- Row Level Security (RLS) & Permissions
-- Enable RLS and grant service_role full control, and anon read access.
-- ==============================================================================
ALTER TABLE public.companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.prospects ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.signal_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.usage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.settings ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    -- Companies policies
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'companies' AND policyname = 'service_role_all_companies') THEN
        CREATE POLICY service_role_all_companies ON public.companies FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    -- Prospects policies
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'prospects' AND policyname = 'service_role_all_prospects') THEN
        CREATE POLICY service_role_all_prospects ON public.prospects FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    -- Outbox policies
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'outbox' AND policyname = 'service_role_all_outbox') THEN
        CREATE POLICY service_role_all_outbox ON public.outbox FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    -- Campaigns policies
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'campaigns' AND policyname = 'service_role_all_campaigns') THEN
        CREATE POLICY service_role_all_campaigns ON public.campaigns FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    -- Signal codes policies
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'signal_codes' AND policyname = 'service_role_all_signal_codes') THEN
        CREATE POLICY service_role_all_signal_codes ON public.signal_codes FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    -- Observations policies
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'observations' AND policyname = 'service_role_all_observations') THEN
        CREATE POLICY service_role_all_observations ON public.observations FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    -- Runs policies
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'runs' AND policyname = 'service_role_all_runs') THEN
        CREATE POLICY service_role_all_runs ON public.runs FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    -- Conversations policies
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'conversations' AND policyname = 'service_role_all_conversations') THEN
        CREATE POLICY service_role_all_conversations ON public.conversations FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    -- Usage events policies
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'usage_events' AND policyname = 'service_role_all_usage_events') THEN
        CREATE POLICY service_role_all_usage_events ON public.usage_events FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    -- Settings policies
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'settings' AND policyname = 'service_role_all_settings') THEN
        CREATE POLICY service_role_all_settings ON public.settings FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;
