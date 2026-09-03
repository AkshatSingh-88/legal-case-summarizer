# Phase A Architecture Specification — Case Workspace Web Application

## 1. Product Architecture Overview

The Legal Case Summarizer is evolving from a standalone PDF analysis engine into a multi-tenant, cloud-native **Case Workspace Web Application**.

### 1.1 The Case Workspace Concept
A legal matter rarely consists of a single isolated PDF. Legal practitioners and litigants deal with multi-document case bundles (e.g., Petitions, Affidavits, Written Statements, Replications, Exhibits, and Court Orders). 

The application centers around a **Case Workspace**:
- **Case Bundle Container**: A user creates a Case (e.g., *"Alpha Corp v. Beta LLC — Commercial Suit"*) and uploads all associated legal PDF documents into that case workspace.
- **Unified Pipeline Processing**: All uploaded documents for a case pass through a shared ingestion pipeline:
  $$\text{PDF Extraction} \rightarrow \text{OCR} \rightarrow \text{NLP Evidence Extraction} \rightarrow \text{Adaptive Chunking}$$
- **Tool Branching**:
  1. **Summarize Case** (*Active Milestone*): Synthesizes chunk-level, file-level, and cross-file case analyses into a comprehensive **Detailed Analysis** with interactive provenance citations.
  2. **Chat with Documents / RAG** (*Future Milestone*): Generates semantic embeddings for chunks and enables conversational retrieval-augmented question answering.

---

## 2. Domain Model & Entity Specifications

```text
User (Supabase Auth)
  └── Case (Workspace)
        ├── Documents [PDF objects in Supabase Storage]
        │     ├── Pages [Extracted text & OCR metadata]
        │     └── Chunks [Adaptive token chunks & evidence metadata]
        ├── Processing Jobs [Async state machine execution]
        └── Summary [DetailedAnalysis content + citations]
```

### 2.1 Entity Definitions & Field Catalog

#### 1. User
Managed via **Supabase Auth**. The application references users by their standard UUID `id`.
- `id`: UUID (Primary Key)
- `email`: string
- `created_at`: timestamp with time zone
- `auth_metadata`: JSON object

#### 2. Case
The top-level workspace unit.
- `id`: UUID (Primary Key)
- `user_id`: UUID | None (Foreign Key to auth.users, nullable for guest sessions)
- `guest_session_id`: UUID | None (Nullable, set for unauthenticated sessions)
- `title`: string (Default: "Untitled Case")
- `status`: CaseStatus enum (`draft` | `ready` | `processing` | `completed` | `failed`)
- `retention_type`: RetentionType enum (`temporary` | `persistent`)
- `expires_at`: timestamp with time zone | None (Populated when retention_type is `temporary`)
- `created_at`: timestamp with time zone
- `updated_at`: timestamp with time zone

#### 3. Document
A single legal PDF uploaded to a Case.
- `id`: UUID (Primary Key)
- `case_id`: UUID (Foreign Key to Case, CASCADE on delete)
- `filename`: string (Original file name)
- `content_type`: string (e.g., `application/pdf`)
- `file_size`: integer (Size in bytes)
- `document_type`: DocumentType enum (Default: `unknown`)
  - Allowed values: `unknown`, `petition`, `appeal`, `application`, `interlocutory_application`, `affidavit`, `reply`, `written_statement`, `evidence`, `judgment`, `order`, `chronology`, `report`, `memo`, `other`.
  - Document type is *never* required from the user.
  - Cases may contain any number and combination of document types; no predefined document bundle set is required.
  - Document type is not required for processing.
- `document_type_confidence`: float | None (Confidence score if inferred; inference is deferred beyond Phase A, filename inference is not guaranteed).
- `storage_path`: string (Internal path inside Supabase Storage bucket; raw bytes are NEVER stored in PostgreSQL)
- `page_count`: integer | None
- `processing_status`: DocumentProcessingStatus enum (`pending` | `uploaded` | `processed` | `failed`)
- `retention_type`: RetentionType enum (`temporary` | `persistent`)
- `expires_at`: timestamp with time zone | None
- `created_at`: timestamp with time zone
- `updated_at`: timestamp with time zone

#### 4. Page
Page-level extracted text and OCR processing records.
- `id`: UUID (Primary Key)
- `document_id`: UUID (Foreign Key to Document, CASCADE on delete)
- `page_number`: integer (1-indexed page sequence)
- `extracted_text`: text
- `ocr_status`: OcrStatus enum (`not_needed` | `performed` | `failed`)
- `metadata`: JSON object (`char_count`, `word_count`, `ocr_confidence`, etc.)
- `created_at`: timestamp with time zone

#### 5. Chunk
Adaptive token chunk derived from pages and evidence extraction.
- `id`: UUID (Primary Key)
- `document_id`: UUID (Foreign Key to Document, CASCADE on delete)
- `chunk_index`: integer
- `page_range`: integer[] (List of page numbers covered by this chunk)
- `text`: text
- `metadata`: JSON object (Evidence scores, section labels, token counts)
- `embedding`: float[] | None (Vector column reserved for future RAG milestone)
- `created_at`: timestamp with time zone

#### 6. ProcessingJob
Asynchronous job tracking the pipeline state machine.
- `id`: UUID (Primary Key)
- `case_id`: UUID (Foreign Key to Case, CASCADE on delete)
- `job_type`: JobType enum (`summary` | `embedding` | `rag`)
- `status`: JobStatus enum (`queued` | `processing` | `completed` | `failed` | `cancelled`)
- `progress`: float (0.0 to 1.0)
- `current_stage`: PipelineStage enum (`queued` | `ingestion` | `ocr` | `evidence` | `chunking` | `llm_chunk` | `file_synthesis` | `case_synthesis` | `presentation` | `completed` | `failed`)
- `error_message`: string | None
- `cancel_requested`: boolean (Default: false)
- `created_at`: timestamp with time zone
- `started_at`: timestamp with time zone | None
- `completed_at`: timestamp with time zone | None

#### 7. Summary
The final structured synthesis output for a case. Exactly **one active Detailed Summary** exists per Case (no version history).
- `id`: UUID (Primary Key)
- `case_id`: UUID (Foreign Key to Case, Unique, CASCADE on delete)
- `summary_type`: SummaryType enum (`detailed`)
- `status`: SummaryStatus enum (`generating` | `ready` | `failed` | `stale`)
- `content`: JSON object (Conforms to canonical `DetailedAnalysis` schema containing `sections[]`, items, timeline events, and resolved citations)
- `created_at`: timestamp with time zone
- `updated_at`: timestamp with time zone

---

## 3. Core Architectural Invariants

### 3.1 Strict Ownership XOR Rule
At all times, every Case must have an unambiguous owner. Exactly one of `user_id` or `guest_session_id` must be set:
$$\left( \text{user\_id IS NOT NULL} \oplus \text{guest\_session\_id IS NOT NULL} \right) = \text{TRUE}$$
- If `user_id` is set $\rightarrow$ `guest_session_id` MUST be `NULL` and `retention_type` defaults to `persistent`.
- If `guest_session_id` is set $\rightarrow$ `user_id` MUST be `NULL`, `retention_type` MUST be `temporary`, and `expires_at` MUST be populated.
- A case can NEVER have both `user_id` and `guest_session_id` populated, and can NEVER have both `NULL`.

### 3.2 Guest Session Security & Server-Side Expiry
- **Cryptographic Credential**: The guest session token (`session_token` passed in `X-Guest-Session-ID`) is an opaque, high-entropy, cryptographically secure credential generated server-side. Possession of this token is required to access guest-owned resources. It is not an enumerable or guessable identifier.
- **Server-Side Expiry Lifecycle**: Guest cases are temporary; expiry is tracked server-side via an explicit `expires_at` timestamp and the guest session's `last_activity_at` lifecycle state. Browser/tab closure is not directly detectable as a reliable backend event. The product intention of 48-hour temporary retention is managed through server-side expiry semantics. Expired resources become inaccessible and are scheduled for background deletion.

### 3.3 Decoupled Document vs. Case Retention
Case retention and original document retention are separate, independently controllable settings:
1. **Case & Summary Retention**: A user may retain their case summary, structural claims, timeline, and issues permanently.
2. **Original PDF Document Retention**: A user or enterprise policy may request that raw PDF files in storage be purged after analysis is completed (or within a short TTL) for data minimization and confidentiality.
3. The system supports deleting physical document files in storage while preserving the computed structured `Summary` record.

### 3.4 Summary Stale Lifecycle Invalidation
For this milestone, there is a single active Summary per Case.
- **Stale Invalidation Rule**: If a Document is added, deleted, or replaced after a Case has been successfully summarized, the existing Summary status becomes `stale`.
- A stale summary requires reprocessing the Case to generate a current, synchronized summary.

---

## 4. Processing Job Lifecycle & State Machine

```text
       [POST /cases/{id}/process]
                   │
                   ▼
               ┌────────┐
               │ QUEUED │ ◄────── Initial state returned immediately (202 Accepted)
               └───┬────┘
                   │ Worker picks up job
                   ▼
              ┌────────────┐
              │ PROCESSING │ ◄── Worker updates progress & current_stage
              └───┬────────┘
                  │
        ┌─────────┼──────────────┐
        │         │              │
        ▼         ▼              ▼
  ┌───────────┐ ┌────────┐ ┌───────────┐
  │ COMPLETED │ │ FAILED │ │ CANCELLED │
  └───────────┘ └────────┘ └───────────┘
```

### 4.1 Processing Prerequisites & Duplicate Prevention
- **Prerequisites**:
  - The Case exists and is owned by the caller.
  - At least one successfully uploaded Document exists in the Case. If no uploaded document exists, the request is rejected with `409 Conflict` (`NO_DOCUMENTS_UPLOADED`).
  - Processing cannot begin while any document upload is still in progress.
- **Duplicate Processing Guard**:
  - If a job of the same `job_type` is currently in `queued` or `processing` state for that Case, a new processing request is rejected with `409 Conflict` (`ACTIVE_JOB_EXISTS`), returning the existing `job_id` in error details.

### 4.2 Cooperative Cancellation Protocol
- **State Transitions**:
  - `queued` $\rightarrow$ `cancelled` (immediate cancellation if not yet running).
  - `processing` $\rightarrow$ `cancel_requested` $\rightarrow$ `cancelled` (worker checks cooperative cancellation between major pipeline stages).
- **Invalid State Guard**:
  - Attempting to cancel a job that is already `completed`, `failed`, or `cancelled` returns `409 Conflict` (`INVALID_JOB_STATE`).

---

## 5. Guest $\rightarrow$ Authenticated Claim Flow

1. An anonymous user enters the application, creating a guest session (`POST /api/v1/guest/sessions`), receiving an opaque `session_token`.
2. The guest uploads documents, creates a Case, and triggers summarization. All records are tagged with `guest_session_id` and `retention_type = 'temporary'`.
3. The guest registers or logs in via Supabase Auth on the frontend, receiving a verified JWT.
4. The frontend calls `POST /api/v1/guest/claim` with `X-Guest-Session-ID` and `Authorization: Bearer <JWT>`.
5. The backend executes an atomic claim transaction:
   - Validates both the JWT and the guest session token.
   - Selects all cases currently owned by that guest session.
   - Updates eligible cases:
     - Sets `user_id = <authenticated user id>`
     - Sets `guest_session_id = NULL`
     - Sets `retention_type = 'persistent'`
     - Sets `expires_at = NULL`
   - Preserves all associated Documents, Summaries, and Jobs intact.
   - Invalidates/detaches the guest session so claimed cases cannot be re-claimed.
6. **Edge Cases**:
   - Expired guest session $\rightarrow$ Rejected (`401 Unauthorized` / `400 Bad Request`).
   - No eligible cases $\rightarrow$ Returns `200 OK` with `claimed_count = 0`.
   - Already claimed cases are never transferred again.

---

## 6. Provenance Scope: Page-Level vs. Exact Highlighting

- **Current Milestone Scope**: Provenance citations operate at the **page level** (identifying specific `document_id`, `filename`, `doc_label`, `page_start`, and `page_end` / `pages`).
- **Deferred Enhancement**: Exact text bounding boxes, line-level highlighting, and character offset tracking are explicitly deferred to future milestones, as they require fine-grained coordinate/offset extraction during OCR and layout parsing.

---

## 7. Orchestration Boundary & Pipeline Integration

The existing backend engine in `backend/app/` represents the pure, deterministic analysis kernel:
- `backend/app/ingestion/` (PDF loading, quality assessment, OCR fallback)
- `backend/app/nlp/` (Evidence scoring, TextRank, TF-IDF, entity extraction)
- `backend/app/chunking/` (Evidence-aware adaptive chunking)
- `backend/app/llm/` (Chunk-level LLM analysis)
- `backend/app/file/` (File-level consolidation and `SRC-xxx` provenance mapping)
- `backend/app/case/` (Cross-file synthesis and `DOC-xxx:SRC-xxx` provenance consolidation)
- `backend/app/presentation/` (DetailedAnalysis dynamic section assembly and citation resolution)

### Boundary Rules:
1. **Zero Modifications to Pipeline Kernel**: The web/API layer wraps the engine without modifying any of its internal logic, schemas, or prompt templates.
2. **Orchestrator Role**: The future job runner/worker downloads PDFs from Supabase Storage to a localized temporary workspace, executes the existing pipeline synchronously in memory, persists the resulting `DetailedAnalysis` to the database `summary` table, and cleans up local scratch files.
