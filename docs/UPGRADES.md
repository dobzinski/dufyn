# Database upgrades

Versioned SQL files are preserved in `docs/sql/`. For an existing installation,
copy only the pending upgrades to `database/upgrade/`, using numeric prefixes to
apply them in release order. Follow [Restore and upgrades](RESTORE.md) for Docker
and Kubernetes execution instructions. Keep the original versioned files; the
execution copies are removed after success.

| Version | SQL | Description |
| --- | --- | --- |
| 0.1.2 | [v0.1.2.sql](sql/v0.1.2.sql) | Adds the Network DHCP flag, nullable current asset IP and last known IP, MAC address storage, hostname index, and internal discovery identification and cycle reconciliation fields. Preserves existing asset IPs as last known addresses. |
