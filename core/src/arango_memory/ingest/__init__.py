"""Ingestion pipeline (DESIGN.md §8): PII redaction, multi-stage extraction,
write-time conflict detection, prospective indexing (full mode), durable writes.

Step 0 implements a minimal path: store a raw episode + a memory record with an
idempotency key. Extraction and enrichment are added in later steps.
"""
