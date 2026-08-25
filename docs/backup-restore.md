# Backup and restore

Milestone 2 supports report-only retention and documented backup/restore for the local
PostgreSQL database plus filesystem artifacts. These procedures do not delete source data.

## PostgreSQL logical backup

Start the local infrastructure first:

```bash
make infra-up
```

Create a compressed logical dump inside the PostgreSQL container:

```bash
docker compose -f deploy/docker-compose.infra.yml --project-name rf-sensor \
  exec -T postgres sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/rf_platform.dump'
```

Copy the dump to the host if it must be moved to another machine:

```bash
docker cp rf-platform-postgres:/tmp/rf_platform.dump .data/backups/rf_platform.dump
```

## PostgreSQL restore check

Always restore into a disposable database first:

```bash
docker compose -f deploy/docker-compose.infra.yml --project-name rf-sensor \
  exec -T postgres sh -lc \
  'dropdb -U "$POSTGRES_USER" --if-exists rf_platform_restore_check &&
   createdb -U "$POSTGRES_USER" rf_platform_restore_check &&
   pg_restore -U "$POSTGRES_USER" -d rf_platform_restore_check /tmp/rf_platform.dump &&
   psql -U "$POSTGRES_USER" -d rf_platform_restore_check -c "select count(*) from sensors;"'
```

Drop the disposable database after the check:

```bash
docker compose -f deploy/docker-compose.infra.yml --project-name rf-sensor \
  exec -T postgres sh -lc 'dropdb -U "$POSTGRES_USER" --if-exists rf_platform_restore_check'
```

## Artifact backup

Back up the configured `RF_ARTIFACT_ROOT` directory. With the default local settings:

```bash
mkdir -p .data/backups
tar -C .data -czf .data/backups/artifacts.tgz artifacts
```

Restore to a disposable directory and compare file counts before using the archive:

```bash
mkdir -p .data/restore-check
tar -C .data/restore-check -xzf .data/backups/artifacts.tgz
find .data/artifacts -type f | wc -l
find .data/restore-check/artifacts -type f | wc -l
```

## Automated verification

The repository includes a verification script that creates a PostgreSQL dump, restores it into
`rf_platform_restore_check`, checks key table counts, creates an artifact archive, and restores it
to a disposable directory:

```bash
make infra-up
make migrate
conda run -n rf-intel python scripts/verify_backup_restore.py
```

The script writes temporary backup files under `.data/backups`, which is ignored by Git.
