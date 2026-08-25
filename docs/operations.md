# Operations

1. Create and activate `rf-intel` from `environment.yml`.
2. Copy `.env.example` to `.env` and change credentials/tokens for the local deployment.
3. Start infrastructure with `make infra-up`.
4. Apply migrations with `make migrate`.
5. Seed profiles with `make seed`.
6. Run API, worker, simulated sensor, and dashboard with their Make targets, or run the full
   simulated flow with `make demo`.

Backups and workstation migration are deferred until Milestone 2+.
