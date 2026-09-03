# API Contract Specification — `/api/v1`

## 1. Overview & Protocol Standards

The Legal Case Summarizer Web API is versioned under `/api/v1`.

### 1.1 Base URL & Content Negotiation
- **Base URL**: `/api/v1`
- **Request Format**: `application/json` (except document upload which is `multipart/form-data`)
- **Response Format**: `application/json` (except PDF report download which is `application/pdf`)
- **Character Encoding**: `UTF-8`

### 1.2 Authentication & Caller Ownership Context
Authentication and ownership context are governed by strict header rules:

1. **Authenticated Context**:
   - Supplied via Supabase Auth JWT in the `Authorization` header:
     ```http
     Authorization: Bearer <supabase_jwt_token>
     ```
2. **Guest Context**:
   - Supplied via an opaque, high-entropy cryptographic session credential token in the header:
     ```http
     X-Guest-Session-ID: <session_token>
     ```
3. **Ownership Context Ambiguity Rules** (for ownership-context endpoints such as `POST /api/v1/cases`, `GET /api/v1/cases`):
   - **JWT only**: Valid authenticated ownership context.
   - **X-Guest-Session-ID only**: Valid guest ownership context.
   - **Both present**: `400 Bad Request` with error code `AMBIGUOUS_OWNERSHIP_CONTEXT` ("Ambiguous ownership context.").
   - **Neither present**: `401 Unauthorized` with error code `UNAUTHORIZED` ("Missing authentication or guest session credentials.").
   - **Exception**: `POST /api/v1/guest/claim` intentionally requires **both** credentials to transfer ownership.

---

## 2. Standardized Error Response Format

All error responses strictly adhere to the following envelope across all error status codes ($4\text{xx}$, $5\text{xx}$):

```json
{
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "message": "Case with ID 'c8f3b174-8b6b-4e12-8821-49fa5cf10321' was not found.",
    "details": null
  }
}
```

### Standard Error Codes:
- `AMBIGUOUS_OWNERSHIP_CONTEXT` (400): Both JWT and Guest credentials supplied on ownership endpoints.
- `UNAUTHORIZED` (401): Missing, malformed, or expired JWT / Guest Session ID.
- `FORBIDDEN` (403): Caller does not own the requested resource.
- `RESOURCE_NOT_FOUND` (404): Resource ID does not exist.
- `ACTIVE_JOB_EXISTS` (409): Duplicate processing job is already queued or processing for the Case (includes existing `job_id` in `details`).
- `NO_DOCUMENTS_UPLOADED` (409): Attempted to process a Case with no successfully uploaded documents.
- `INVALID_JOB_STATE` (409): Attempted to cancel a job that is already completed, failed, or cancelled.
- `VALIDATION_ERROR` (422): Request schema validation failure.
- `NOT_IMPLEMENTED` (501): Feature placeholder in contract skeleton (e.g. PDF report export).

---

## 3. Provenance & Citation Resolution

### 3.1 Inline Provenance Design
Provenance citations are **first-class structural elements embedded directly within the generated Summary content**. There is no separate `/citations` route group.

The citation structure embedded in `CitedAnalysisItem` matches the canonical engine presentation model (`backend.app.presentation.citations.ResolvedCitation`) exactly:

```json
{
  "source_ref": "DOC-001:SRC-001",
  "doc_label": "DOC-001",
  "document_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "filename": "petition.pdf",
  "page_start": 1,
  "page_end": 2,
  "pages": [1, 2]
}
```

### 3.2 Dynamic Viewer Resolution
When a user clicks a citation in the frontend interface:
1. The frontend reads `document_id` and target `page_start`.
2. The frontend calls:
   $$\text{GET } /api/v1/documents/\{document\_id\}/access$$
3. The endpoint returns a temporary signed URL to view the raw PDF directly positioned at the target page.

---

## 4. File Validation & Configuration Constraints

- **Allowed File Type**: `application/pdf` exclusively.
- **Configurable Platform Limits** (values driven by server configuration, not hardcoded in Phase A):
  - `MAX_UPLOAD_FILE_SIZE`: Maximum bytes allowed per individual PDF upload.
  - `MAX_DOCUMENTS_PER_CASE`: Maximum number of documents allowed per Case.
  - `MAX_CASE_TOTAL_SIZE`: Maximum aggregate file bytes allowed across all documents in a Case.

---

## 5. Endpoints Catalog

### 5.1 System

#### `GET /api/v1/system/health`
Checks backend service availability and dependency diagnostics.

- **Headers**: None
- **Request Body**: None
- **Success Status**: `200 OK`
- **Response Schema**:
  ```json
  {
    "status": "ok",
    "version": "0.1.0",
    "environment": "development",
    "timestamp": "2026-09-04T02:30:00Z"
  }
  ```

---

### 5.2 Authentication

#### `GET /api/v1/auth/me`
Resolves user profile and account metadata from a validated Supabase JWT.

- **Headers**: `Authorization: Bearer <supabase_jwt_token>` (Required)
- **Request Body**: None
- **Success Status**: `200 OK`
- **Response Schema**:
  ```json
  {
    "id": "7a3068f8-3e4b-47e1-8848-3606fbfd7541",
    "email": "lawyer@example.com",
    "role": "authenticated",
    "created_at": "2026-09-01T10:00:00Z"
  }
  ```
- **Errors**: `401 Unauthorized`

---

### 5.3 Cases

#### `POST /api/v1/cases`
Creates a new Case workspace.

- **Headers**: `Authorization: Bearer <token>` OR `X-Guest-Session-ID: <token>`
- **Request Body**:
  ```json
  {
    "title": "Alpha Corp v. Beta LLC — Breach of Contract",
    "retention_type": "persistent"
  }
  ```
- **Success Status**: `201 Created`
- **Response Schema**:
  ```json
  {
    "id": "c8f3b174-8b6b-4e12-8821-49fa5cf10321",
    "title": "Alpha Corp v. Beta LLC — Breach of Contract",
    "status": "draft",
    "retention_type": "persistent",
    "expires_at": null,
    "document_count": 0,
    "created_at": "2026-09-04T02:30:00Z",
    "updated_at": "2026-09-04T02:30:00Z"
  }
  ```
- **Errors**: `400 Bad Request` (Ambiguous credentials), `401 Unauthorized` (Missing credentials).

#### `GET /api/v1/cases`
Lists all cases owned by the caller.

- **Headers**: `Authorization: Bearer <token>` OR `X-Guest-Session-ID: <token>`
- **Query Parameters**:
  - `limit`: integer (default: 20, max: 100)
  - `offset`: integer (default: 0)
- **Success Status**: `200 OK`
- **Response Schema**:
  ```json
  {
    "items": [
      {
        "id": "c8f3b174-8b6b-4e12-8821-49fa5cf10321",
        "title": "Alpha Corp v. Beta LLC",
        "status": "ready",
        "retention_type": "persistent",
        "expires_at": null,
        "document_count": 2,
        "created_at": "2026-09-04T02:30:00Z",
        "updated_at": "2026-09-04T02:30:00Z"
      }
    ],
    "total": 1,
    "limit": 20,
    "offset": 0
  }
  ```
- **Errors**: `400 Bad Request`, `401 Unauthorized`.

#### `GET /api/v1/cases/{case_id}`
Retrieves detailed metadata and document listings for a specific case.

- **Headers**: `Authorization: Bearer <token>` OR `X-Guest-Session-ID: <token>`
- **Success Status**: `200 OK`
- **Response Schema**:
  ```json
  {
    "id": "c8f3b174-8b6b-4e12-8821-49fa5cf10321",
    "title": "Alpha Corp v. Beta LLC",
    "status": "ready",
    "retention_type": "persistent",
    "expires_at": null,
    "documents": [
      {
        "id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
        "filename": "petition.pdf",
        "content_type": "application/pdf",
        "file_size": 245120,
        "document_type": "petition",
        "document_type_confidence": null,
        "page_count": 12,
        "processing_status": "processed",
        "created_at": "2026-09-04T02:31:00Z"
      }
    ],
    "created_at": "2026-09-04T02:30:00Z",
    "updated_at": "2026-09-04T02:30:00Z"
  }
  ```
- **Errors**: `403 Forbidden`, `404 Not Found`.

#### `PATCH /api/v1/cases/{case_id}`
Updates case attributes.

- **Headers**: `Authorization: Bearer <token>` OR `X-Guest-Session-ID: <token>`
- **Request Body**:
  ```json
  {
    "title": "Updated Case Title",
    "retention_type": "persistent"
  }
  ```
- **Success Status**: `200 OK`
- **Response Schema**: Returns updated Case schema.

#### `DELETE /api/v1/cases/{case_id}`
Permanently deletes a case and cascades deletion to documents, storage objects, summaries, and jobs.

- **Headers**: `Authorization: Bearer <token>` OR `X-Guest-Session-ID: <token>`
- **Success Status**: `204 No Content`
- **Errors**: `403 Forbidden`, `404 Not Found`.

---

### 5.4 Documents

#### `POST /api/v1/cases/{case_id}/documents`
Uploads one or more legal PDF files to a Case. Features **partial-success semantics** where individual file successes and failures are reported independently.

- **Headers**: `Authorization: Bearer <token>` OR `X-Guest-Session-ID: <token>`
- **Content-Type**: `multipart/form-data`
- **Form Body**: `files`: list of binary PDF uploads
- **Success Status**: `201 Created`
- **Response Schema**:
  ```json
  {
    "results": [
      {
        "filename": "petition.pdf",
        "status": "uploaded",
        "document": {
          "id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
          "case_id": "c8f3b174-8b6b-4e12-8821-49fa5cf10321",
          "filename": "petition.pdf",
          "content_type": "application/pdf",
          "file_size": 245120,
          "document_type": "petition",
          "document_type_confidence": null,
          "page_count": null,
          "processing_status": "uploaded",
          "retention_type": "persistent",
          "expires_at": null,
          "created_at": "2026-09-04T02:32:00Z",
          "updated_at": "2026-09-04T02:32:00Z"
        },
        "error": null
      }
    ],
    "accepted_count": 1,
    "failed_count": 0
  }
  ```

#### `GET /api/v1/cases/{case_id}/documents`
Lists all documents registered in a case.

- **Headers**: `Authorization: Bearer <token>` OR `X-Guest-Session-ID: <token>`
- **Success Status**: `200 OK`
- **Response Schema**: Returns `items: list[DocumentResponse]`.

#### `GET /api/v1/documents/{document_id}`
Retrieves single document metadata.

- **Headers**: `Authorization: Bearer <token>` OR `X-Guest-Session-ID: <token>`
- **Success Status**: `200 OK`
- **Response Schema**: Returns `DocumentResponse`.

#### `GET /api/v1/documents/{document_id}/access`
Generates a short-lived signed URL for accessing the raw PDF in the viewer.

- **Headers**: `Authorization: Bearer <token>` OR `X-Guest-Session-ID: <token>`
- **Success Status**: `200 OK`
- **Response Schema**:
  ```json
  {
    "document_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "access_url": "https://storage.supabase.co/storage/v1/object/sign/legal-docs/petition.pdf?token=...",
    "expires_at": "2026-09-04T03:30:00Z"
  }
  ```

#### `DELETE /api/v1/documents/{document_id}`
Deletes a single document and purges its blob from storage.

- **Headers**: `Authorization: Bearer <token>` OR `X-Guest-Session-ID: <token>`
- **Success Status**: `204 No Content`

---

### 5.5 Processing & Jobs

#### `POST /api/v1/cases/{case_id}/process`
Enqueues an asynchronous processing job. Returns immediately with `202 Accepted`.

- **Prerequisites**: Case exists, caller owns Case, at least one successfully uploaded Document exists.
- **Headers**: `Authorization: Bearer <token>` OR `X-Guest-Session-ID: <token>`
- **Request Body**:
  ```json
  {
    "job_type": "summary"
  }
  ```
- **Success Status**: `202 Accepted`
- **Response Schema**:
  ```json
  {
    "id": "f5e6d7c8-1a2b-4c3d-8e9f-0a1b2c3d4e5f",
    "case_id": "c8f3b174-8b6b-4e12-8821-49fa5cf10321",
    "job_type": "summary",
    "status": "queued",
    "progress": 0.0,
    "current_stage": "queued",
    "error_message": null,
    "cancel_requested": false,
    "created_at": "2026-09-04T02:35:00Z",
    "started_at": null,
    "completed_at": null
  }
  ```
- **Errors**:
  - `409 Conflict` (`ACTIVE_JOB_EXISTS`): Active job already running for this case (details contain existing `job_id`).
  - `409 Conflict` (`NO_DOCUMENTS_UPLOADED`): Case has no uploaded documents.

#### `GET /api/v1/jobs/{job_id}`
Polls job progress, status, and current execution stage.

- **Headers**: `Authorization: Bearer <token>` OR `X-Guest-Session-ID: <token>`
- **Success Status**: `200 OK`
- **Response Schema**: Returns `JobResponse`.

#### `POST /api/v1/jobs/{job_id}/cancel`
Requests cooperative cancellation of a running or queued job.

- **Headers**: `Authorization: Bearer <token>` OR `X-Guest-Session-ID: <token>`
- **Success Status**: `200 OK`
- **Response Schema**:
  ```json
  {
    "id": "f5e6d7c8-1a2b-4c3d-8e9f-0a1b2c3d4e5f",
    "status": "processing",
    "cancel_requested": true,
    "message": "Cancellation request submitted."
  }
  ```
- **Errors**: `409 Conflict` (`INVALID_JOB_STATE` if job is already completed, failed, or cancelled).

---

### 5.6 Summaries

#### `GET /api/v1/cases/{case_id}/summary`
Retrieves presentation-ready, structured Detailed Analysis for the case.

- **Headers**: `Authorization: Bearer <token>` OR `X-Guest-Session-ID: <token>`
- **Success Status**: `200 OK`
- **Response Schema**:
  ```json
  {
    "id": "e1f2a3b4-5c6d-7e8f-9a0b-1c2d3e4f5a6b",
    "case_id": "c8f3b174-8b6b-4e12-8821-49fa5cf10321",
    "summary_type": "detailed",
    "status": "ready",
    "content": {
      "case_id": "c8f3b174-8b6b-4e12-8821-49fa5cf10321",
      "section_count": 2,
      "sections": [
        {
          "section_id": "sec_overview",
          "title": "Executive Overview",
          "section_type": "text",
          "order": 1,
          "text": "Commercial dispute regarding non-delivery of goods.",
          "items": null,
          "relationships": null,
          "timeline_events": null,
          "source_refs": ["DOC-001:SRC-001"]
        }
      ],
      "case_coverage": 1.0,
      "status": "complete",
      "confidence": 0.95,
      "uncertainty": null,
      "meta": { "document_count": 2 },
      "analysis_mode": "detailed",
      "is_preliminary": false
    },
    "created_at": "2026-09-04T02:36:00Z",
    "updated_at": "2026-09-04T02:36:00Z"
  }
  ```
- **Errors**: `404 Not Found` (Summary not yet generated).

#### `GET /api/v1/cases/{case_id}/summary/pdf`
Downloads an exportable, formatted PDF report.

- **Headers**: `Authorization: Bearer <token>` OR `X-Guest-Session-ID: <token>`
- **Production Contract**:
  - `200 OK`
  - `Content-Type: application/pdf`
  - `Content-Disposition: attachment; filename="CaseSummary_{case_id}.pdf"`
- **Phase A Skeleton Behavior**: Returns `501 Not Implemented` with standardized error response.

---

### 5.7 Guest Management

#### `POST /api/v1/guest/sessions`
Initializes a new temporary guest session with cryptographic token and server-side activity tracking.

- **Headers**: None
- **Request Body**: None
- **Success Status**: `201 Created`
- **Response Schema**:
  ```json
  {
    "guest_session_id": "3d4e5f6a-7b8c-9d0e-1f2a-3b4c5d6e7f8a",
    "session_token": "gst_a9F8z1K3mP0qR2sT4vU6wX8yZ...",
    "last_activity_at": "2026-09-04T02:30:00Z",
    "expires_at": "2026-09-05T02:30:00Z",
    "created_at": "2026-09-04T02:30:00Z"
  }
  ```

#### `POST /api/v1/guest/claim`
Transfers ownership of all cases in a guest session to the authenticated user.

- **Headers**:
  - `Authorization: Bearer <supabase_jwt_token>` (Required)
  - `X-Guest-Session-ID: <session_token>` (Required)
- **Request Body**: None
- **Success Status**: `200 OK`
- **Response Schema**:
  ```json
  {
    "claimed_case_ids": [
      "c8f3b174-8b6b-4e12-8821-49fa5cf10321"
    ],
    "claimed_count": 1,
    "user_id": "7a3068f8-3e4b-47e1-8848-3606fbfd7541",
    "claimed_at": "2026-09-04T02:40:00Z"
  }
  ```
- **Errors**:
  - `401 Unauthorized` (Missing or invalid JWT).
  - `400 Bad Request` (Missing guest session credential).
