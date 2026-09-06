-- Migration: 20260905012000_cases_documents_policies.sql
-- Description: Add RLS policies for backend and guest access on case domain tables

CREATE POLICY "Allow backend access to cases"
ON cases FOR ALL
TO anon, authenticated
USING (true)
WITH CHECK (true);

CREATE POLICY "Allow backend access to documents"
ON documents FOR ALL
TO anon, authenticated
USING (true)
WITH CHECK (true);

CREATE POLICY "Allow backend access to pages"
ON pages FOR ALL
TO anon, authenticated
USING (true)
WITH CHECK (true);

CREATE POLICY "Allow backend access to chunks"
ON chunks FOR ALL
TO anon, authenticated
USING (true)
WITH CHECK (true);

CREATE POLICY "Allow backend access to summaries"
ON summaries FOR ALL
TO anon, authenticated
USING (true)
WITH CHECK (true);

CREATE POLICY "Allow backend access to processing_jobs"
ON processing_jobs FOR ALL
TO anon, authenticated
USING (true)
WITH CHECK (true);
