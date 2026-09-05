-- Duplicate PDF files may share the same SHA-256 hash but have distinct document_id/filename.
ALTER TABLE source_documents DROP CONSTRAINT IF EXISTS source_documents_file_hash_key;
CREATE INDEX IF NOT EXISTS idx_source_documents_file_hash ON source_documents(file_hash);
