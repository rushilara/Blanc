# Blanc schemas

`evidence_record.schema.json` defines a traceable source record.  
`analysis_revision.schema.json` defines a saved question-specific reconstruction.

The `../fixtures/` folder contains **synthetic example content only**. It is safe for frontend development, but **must not** be presented as findings extracted from Narcos or any actual forensic dataset.

These are minimal cross-team contracts. Server-side validation must additionally verify: every cited ID exists in the retrieved, permitted case corpus; every edge references existing graph nodes; no excluded record is cited; timestamps are treated according to precision/kind; and user-authored notes remain distinct from LLM-generated conclusions.

For AI generation, prefer a separate subset of the schema containing only answer/claims/graph/events; assign analysis IDs, excluded IDs, retrieved IDs, and creation timestamps on the trusted server.
