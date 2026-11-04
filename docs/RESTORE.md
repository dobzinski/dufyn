# Restore and upgrades

Both restore and upgrade SQLs are processed by `database/control.sh` at database
container startup. Copying a SQL file alone does not trigger execution. Complete
the file transfer before restarting; the database is temporarily unavailable while
the operation runs.

- Restore: extract `database/restore.sql` from the backup into the installation's
  `database/restore/` directory. Leave only one complete `.sql` dump there. Keep
  the original `PKI_MASTER_KEY` and restore/review the application configuration.
- Upgrade: place `.sql` files in `database/upgrade/` with zero-padded numeric
  prefixes, such as `01_table.sql` and `02_inserts.sql`.

Restore runs first, then upgrades run in ascending filename order. Each SQL is
deleted only after successful execution. If an import fails, the original database
is restored to its original name. On an upgrade failure, startup stops and that SQL
is retained; earlier successful upgrades remain applied. A successful restore keeps
the previous database under a dated name.

### Docker: restart and inspect logs

Compose mounts the installation's `database/restore/` and `database/upgrade/`
directories. After placing the SQLs there, run:

```sh
docker restart dufyn-database
docker logs --since 2m dufyn-database
```

Add `--follow` to the logs command to watch startup until completion.

### Kubernetes: recreate the database Pod and inspect logs

Place the SQLs in `database/restore/` or `database/upgrade/` on the persistent
maintenance volume mounted by the database Pod. Files copied only into the Pod's
internal filesystem will not survive Pod replacement.

With the supplied StatefulSet, delete only its database Pod normally:

```sh
kubectl -n dufyn delete pod dufyn-database-0
kubectl -n dufyn get pod dufyn-database-0 --watch
```

The StatefulSet recreates the Pod with its existing persistent volumes. Once the
replacement container starts, inspect the logs:

```sh
kubectl -n dufyn logs dufyn-database-0 -c dufyn-database --since=2m --follow
```

Keep the StatefulSet and PVCs; do not force-delete the Pod. Adjust the namespace
and Pod name if your installation differs.

These procedures restart the installed database code. To use an updated
`control.sh`, first build/publish and deploy the new database image. A Docker
restart does not replace the image, and Pod recreation with the same image tag
and `imagePullPolicy: IfNotPresent` does not guarantee fetching a new build.
