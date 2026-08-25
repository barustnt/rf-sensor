# Architecture

Milestone 1 runs the central platform on one host: FastAPI backend, PostgreSQL, NATS JetStream,
filesystem artifact storage, mock RF-GPT worker, and Gradio dashboard. Simulated sensors upload
through the API and never connect directly to storage, database, broker, worker, or dashboard.

The API persists accepted captures and analysis jobs, records an outbox event, commits the
database transaction, then publishes to JetStream. If publication fails, pending outbox rows can
be retried without duplicating jobs.
