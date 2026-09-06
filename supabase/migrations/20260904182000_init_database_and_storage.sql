-- Phase B: Initial Database & Storage Schema for Legal Case Summarizer

-- 1. Updated_at trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 2. Guest Sessions Table
CREATE TABLE IF NOT EXISTS guest_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash TEXT UNIQUE NOT NULL,
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. Cases Table
CREATE TABLE IF NOT EXISTS cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    guest_session_id UUID REFERENCES guest_sessions(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'Untitled Case',
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'ready', 'processing', 'completed', 'failed')),
    retention_type TEXT NOT NULL DEFAULT 'persistent' CHECK (retention_type IN ('temporary', 'persistent')),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT case_ownership_xor_check CHECK (
        (user_id IS NOT NULL AND guest_session_id IS NULL)
        OR
        (user_id IS NULL AND guest_session_id IS NOT NULL)
    ),
    CONSTRAINT guest_case_must_be_temporary CHECK (
        guest_session_id IS NULL OR retention_type = 'temporary'
    ),
    CONSTRAINT case_retention_expiry_check CHECK (
        (retention_type = 'temporary' AND expires_at IS NOT NULL)
        OR
        (retention_type = 'persistent' AND expires_at IS NULL)
    )
);

CREATE TRIGGER trg_cases_updated_at
BEFORE UPDATE ON cases
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

-- Indexes on cases
CREATE INDEX IF NOT EXISTS idx_cases_user_id ON cases(user_id) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cases_guest_session_id ON cases(guest_session_id) WHERE guest_session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cases_expires_at ON cases(expires_at) WHERE expires_at IS NOT NULL;

-- 4. Documents Table
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/pdf',
    file_size BIGINT NOT NULL,
    document_type TEXT NOT NULL DEFAULT 'unknown' CHECK (document_type IN (
        'unknown', 'petition', 'appeal', 'application', 'interlocutory_application',
        'affidavit', 'reply', 'written_statement', 'evidence', 'judgment',
        'order', 'chronology', 'report', 'memo', 'other'
    )),
    document_type_confidence DOUBLE PRECISION,
    storage_path TEXT NOT NULL,
    page_count INTEGER,
    processing_status TEXT NOT NULL DEFAULT 'uploaded' CHECK (processing_status IN ('pending', 'uploaded', 'processed', 'failed')),
    retention_type TEXT NOT NULL DEFAULT 'persistent' CHECK (retention_type IN ('temporary', 'persistent')),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT doc_retention_expiry_check CHECK (
        (retention_type = 'temporary' AND expires_at IS NOT NULL)
        OR
        (retention_type = 'persistent' AND expires_at IS NULL)
    )
);

CREATE TRIGGER trg_documents_updated_at
BEFORE UPDATE ON documents
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

CREATE INDEX IF NOT EXISTS idx_documents_case_id ON documents(case_id);

-- 5. Pages Table (Foundation)
CREATE TABLE IF NOT EXISTS pages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    extracted_text TEXT NOT NULL DEFAULT '',
    ocr_status TEXT NOT NULL DEFAULT 'not_needed' CHECK (ocr_status IN ('not_needed', 'performed', 'failed')),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_page_per_doc UNIQUE (document_id, page_number)
);

CREATE INDEX IF NOT EXISTS idx_pages_document_id ON pages(document_id);

-- 6. Chunks Table (Foundation - No embedding vector column in Phase B)
CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    page_range INTEGER[] NOT NULL DEFAULT '{}',
    text TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_chunk_per_doc UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);

-- 7. Processing Jobs Table (Foundation)
CREATE TABLE IF NOT EXISTS processing_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL DEFAULT 'summary' CHECK (job_type IN ('summary', 'embedding', 'rag')),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'processing', 'completed', 'failed', 'cancelled')),
    progress DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    current_stage TEXT NOT NULL DEFAULT 'queued',
    error_message TEXT,
    cancel_requested BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_processing_jobs_case_id ON processing_jobs(case_id);

-- Enforce Phase A duplicate job invariant: at most one active (queued/processing) job per case and job_type
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_job_per_case_and_type
ON processing_jobs (case_id, job_type)
WHERE status IN ('queued', 'processing');

-- 8. Summaries Table (Foundation)
CREATE TABLE IF NOT EXISTS summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id UUID UNIQUE NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    summary_type TEXT NOT NULL DEFAULT 'detailed',
    status TEXT NOT NULL DEFAULT 'generating' CHECK (status IN ('generating', 'ready', 'failed', 'stale')),
    content JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_summary_case_id UNIQUE (case_id)
);

CREATE TRIGGER trg_summaries_updated_at
BEFORE UPDATE ON summaries
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

-- 9. Storage Bucket Provisioning (Private)
INSERT INTO storage.buckets (id, name, public)
VALUES ('legal-case-documents', 'legal-case-documents', false)
ON CONFLICT (id) DO NOTHING;

-- 10. Row Level Security Foundation
ALTER TABLE guest_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE pages ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE processing_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE summaries ENABLE ROW LEVEL SECURITY;

-- User-scoped RLS policies (for direct authenticated access / defense in depth)
CREATE POLICY "Users can manage their own cases"
ON cases FOR ALL
TO authenticated
USING (auth.uid() = user_id)
WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can manage documents of their cases"
ON documents FOR ALL
TO authenticated
USING (EXISTS (SELECT 1 FROM cases WHERE cases.id = documents.case_id AND cases.user_id = auth.uid()))
WITH CHECK (EXISTS (SELECT 1 FROM cases WHERE cases.id = documents.case_id AND cases.user_id = auth.uid()));

CREATE POLICY "Users can view pages of their documents"
ON pages FOR SELECT
TO authenticated
USING (EXISTS (
    SELECT 1 FROM documents
    JOIN cases ON cases.id = documents.case_id
    WHERE documents.id = pages.document_id AND cases.user_id = auth.uid()
));

CREATE POLICY "Users can view chunks of their documents"
ON chunks FOR SELECT
TO authenticated
USING (EXISTS (
    SELECT 1 FROM documents
    JOIN cases ON cases.id = documents.case_id
    WHERE documents.id = chunks.document_id AND cases.user_id = auth.uid()
));

CREATE POLICY "Users can view summaries of their cases"
ON summaries FOR SELECT
TO authenticated
USING (EXISTS (SELECT 1 FROM cases WHERE cases.id = summaries.case_id AND cases.user_id = auth.uid()));

CREATE POLICY "Users can manage jobs of their cases"
ON processing_jobs FOR ALL
TO authenticated
USING (EXISTS (SELECT 1 FROM cases WHERE cases.id = processing_jobs.case_id AND cases.user_id = auth.uid()));
