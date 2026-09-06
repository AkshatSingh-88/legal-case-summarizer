-- Phase B: Add RLS policies for guest sessions and private storage bucket objects

-- 1. Guest Sessions policies
-- Guest sessions contain only opaque SHA-256 token hashes and timestamps (no user PII).
-- Allow session creation and hash lookup.
CREATE POLICY "Allow guest session operations"
ON guest_sessions FOR ALL
USING (true)
WITH CHECK (true);

-- 2. Storage Objects policies for legal-case-documents bucket
-- Allow bucket operations for legal-case-documents
CREATE POLICY "Allow legal-case-documents bucket operations"
ON storage.objects FOR ALL
USING (bucket_id = 'legal-case-documents')
WITH CHECK (bucket_id = 'legal-case-documents');
