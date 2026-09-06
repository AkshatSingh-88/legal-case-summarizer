-- Phase B: Harden closed-value and range database constraints

-- 1. Documents constraints
ALTER TABLE documents
ADD CONSTRAINT chk_documents_content_type CHECK (content_type = 'application/pdf');

ALTER TABLE documents
ADD CONSTRAINT chk_documents_file_size CHECK (file_size >= 0);

ALTER TABLE documents
ADD CONSTRAINT chk_documents_confidence CHECK (
    document_type_confidence IS NULL OR (document_type_confidence >= 0.0 AND document_type_confidence <= 1.0)
);

-- 2. Processing Jobs constraints
ALTER TABLE processing_jobs
ADD CONSTRAINT chk_processing_jobs_progress CHECK (progress >= 0.0 AND progress <= 100.0);

-- 3. Summaries constraints
ALTER TABLE summaries
ADD CONSTRAINT chk_summaries_summary_type CHECK (summary_type IN ('detailed'));
