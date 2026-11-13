# Scanner execution

All complete and partial runs use the same functions in scanner/app/main.py.
There is one scheduler, bounded executor infrastructure and shared task execution;
complete runs do not contain a second implementation of each check.

## Macros

| Macro | Work | Scope |
| --- | --- | --- |
| dns | Complete AXFR imports of configured forward zones | dns.server / dns.zones, authenticated Site |
| ptr | Reverse queries for every usable network IP, independent of ping/assets | Enabled assigned IPv4 Networks with PTR = Yes, when ptr.enabled |
| assets | ICMP discovery and reachability; optional NetBIOS name/MAC collection | Enabled assigned IPv4 Networks |
| targets | Resolve and ping configured Targets; update status/latency | Eligible Targets in scanner Site |
| ports | Asset TCP/UDP ports and Target HTTP/HTTPS/HTTP3 service checks | Assigned-network assets and eligible Site Targets |
| exclusive | Inventory ports absent from the effective scan configuration; individual destination only | Assigned-network assets and eligible Site Targets |
| certificates | TLS/QUIC certificates from existing open inventory ports | Assigned-network assets and eligible Site Targets |

Canonical order: **dns -> ptr -> assets -> targets -> ports -> exclusive -> certificates**.
Earlier stages are not inserted automatically. Ports-only uses current inventory;
certificates-only uses existing open ports; PTR-only scans network addresses
without discovery. A /24 PTR run queries 254 usable IPs, even with no assets.

Target-only runs do not probe services or collect certificates. Ports can perform
TLS/QUIC handshakes to identify services, but certificate persistence belongs to
the certificates stage. Certificate-only runs do not change port state or ICMP
status. Failed certificate probes preserve prior certificate inventory.

The `exclusive` macro calculates differences in PostgreSQL with `NOT EXISTS`,
comparing port number and transport protocol. It checks existing inventory ports
(both open and closed), including manually registered ones, without adding them
to any catalog or another destination. Assets use the most specific enabled
Network assigned to this scanner, with that Network's Site inheritance or custom
port selection. Targets have no Network relationship: they remain scoped to the
scanner Site and are compared with their own Target catalog assignments.
Destinations with no differences do not create tasks. Existing owner-level leases,
configuration snapshots, heartbeats and idempotent results apply to exclusive
checks too. Changes to port selections invalidate outstanding results.

Run just these checks with `python -m app.main --scans exclusive --mode once`.
The all cycle includes exclusive immediately after ports, before certificates.
Target checks retain the normal HTTP/HTTPS/HTTP3 behavior; Asset checks retain
TCP/Nmap UDP behavior. Confirmed results update the existing inventory, preserving
its service description; inconclusive probes preserve the previous confirmed state.
Exclusive checks also detect and save TLS/QUIC certificates, including when run
without the certificates macro. Normal ports-only checks leave certificate
storage to the following certificates stage.

## Selection and switches

```sh
docker compose -f deploy/docker/compose.yaml build scanner
docker compose -f deploy/docker/compose.yaml run --rm scanner
docker compose -f deploy/docker/compose.yaml run --rm scanner python -m app.main --scans targets --mode once
docker compose -f deploy/docker/compose.yaml run --rm scanner python -m app.main --scans dns,ptr --mode once
docker compose -f deploy/docker/compose.yaml run --rm scanner python -m app.main --scans assets,ports,exclusive,certificates --mode once
```

Omitting --scans selects all. Comma-separated or separate macro names are accepted;
unknown/empty selections are rejected. Legacy execution.scans and SCANNER_SCANS
are ignored. Selection is by function, not by an individual host/IP selector.

- `targets.enabled` defaults true. Enable it on only one scanner per Site; false
  disables Target ping/ports/exclusive/certificates even for explicit macro selections.
- `dns.enabled` controls imports in all mode. Explicit --scans dns activates them
  but requires a configured authoritative server and nonempty zones.
- `ptr.enabled` is a root-level switch, independent of dns.enabled. Network PTR
  opt-in and scanner assignment are still required. `ptr.server` selects the DNS
  server for reverse queries; null uses the container/Pod resolver, not necessarily
  the host resolver or AXFR server. No assigned eligible networks means no PTR tasks.
- With all selected, DNS enabled and no assigned enabled networks, the DNS-only
  role runs only DNS/PTR and skips the remaining stages; PTR has no eligible network.

## Asset reconciliation and NetBIOS

Selected, enabled DNS/PTR stages collect before discovery. Source flags also
control whether reconciliation can read the Site's existing DNS/PTR tables,
independently of macro selection; PTR additionally requires Network PTR = Yes.
Thus endpoints/assets-only runs can reuse existing names without new DNS queries.
Missing data on the first run does not prevent recording a responding host.

Discovery observations use the internal identification queue. Existing assets
receive ICMP status and latency immediately. After assets discovery completes,
Controller reconciles that cycle before ports and certificates begin, preserving
original ping timestamps and metadata. Successful reconciliation removes the
cycle's entries; failures are retried and reported with observations retained.
With both sources disabled, the queue is still consumed without reading DNS/PTR.
Standalone DNS/PTR and exclusive runs do not discover assets or create this queue.

For DHCP networks, identity matching prefers a unique NetBIOS MAC, then the DNS A
name, then PTR. A contradictory known MAC is not overridden by a name match.
Without those identifiers, IP matching remains a fallback with DHCP reuse risks.
For DHCP = No, Site/IP remains the identity even when MAC or names change.

NetBIOS collection is optional in `scanner/config.yaml` and the Kubernetes scanner
ConfigMap:

```yaml
netbios:
  enabled: false
  port: 137
  timeout_seconds: 2
  attempts: 2
```

When enabled, the assets macro queries discovered hosts over UDP, independently
of DHCP and DNS/PTR switches. It fills an empty Name and stores a valid MAC when
provided; it does not supply Hostname. A unique MAC can identify DHCP assets. Missing
or zero MAC values do not erase a stored MAC. Availability depends on the target
service and network access. These settings are synchronized to Controller and
displayed in Scanner details.

See [Asset hostnames and DHCP](DNS.md#asset-hostnames-and-dhcp) for name precedence
and identity limitations.

## Modes and timing

`once` executes the selected group and exits. `continuous` repeats the same
group according to execution.interval_seconds, anchored to process start.
Each process starts its first cycle immediately. Runtime is deducted from the
wait; missed intervals are skipped rather than queued or overlapped. Restarting
starts a new schedule. There is no separate DNS/PTR interval.

For a wall-clock schedule, use once with an external scheduler and avoid overlap.
The Kubernetes CronJob supplies hourly scheduling while each Job runs once.
Partial CLI runs should normally use --mode once. Ctrl+C/SIGTERM stop probes and
attempt cancellation; forced termination cannot run cleanup, so leases expire.
A new scan starts new tasks rather than resuming a partially completed subnet.

## Reservations and workers

Claims bind the authorized scanner/Site and snapshot configuration. Results cannot
choose arbitrary owners, scope or networks. Heartbeats refresh leases (default
90 seconds), independent of the scan interval. Completed result replays do not
insert duplicates; incomplete network batches cannot commit as complete.

Network discovery and network PTR use discovery_task with distinct scopes.
PTR-only completion never marks assets inactive or creates assets. Target
operations share target_task reservations; asset port/certificate operations use
asset_port_task. The legacy per-asset PTR API remains for compatibility, but the
current PTR macro uses network tasks. A live network lease can defer another
network operation until completion/expiry.

Network coordinators use a separate executor from bounded probe workers. Stages
complete before the next begins; the shared limiter also bounds DNS requests.
Nmap UDP uses the task's assigned UDP catalog. QUIC/HTTP3 attempts are further
restricted by udp.quic.ports (default [443, 8080, 8443]); enabled=false or an empty
list skips QUIC. The allowlist never introduces an unassigned port. Per-host timeouts bound UDP work. No response from UDP is not treated
as a verified positive service response.

## PTR persistence

Only answered queries with valid domains are stored. Successful no-PTR/NXDOMAIN
responses remove obsolete answers for that Site/IP/resolver; failures preserve
last known positives. Query outcomes for all IPs are sent transiently to validate
full network coverage, not stored as empty rows.


## Per-port response measurements

Assets report `response_time_ms` for confirmed open ports. TCP measures connection
establishment with a monotonic clock, excluding the limiter and certificate probe.
UDP DNS/53 and NTP/123 use a bounded, rate-limited application query after Nmap
confirms the port open. Only valid replies produce a duration. Successful QUIC
handshakes also provide a duration for permitted UDP ports. Nmap batch runtime and
host-wide RTT estimates are never assigned to individual ports. Other UDP services
without a reliable per-port measurement remain null. Target HTTP/HTTPS/HTTP3 timing
retains its existing meaning.

Controller stores these measurements in the existing `port.response_time_ms` field;
Manager `/ports` exposes them as Response. Zero is a valid measurement. Confirmed
closed checks clear the measurement; inconclusive checks retain previous inventory
and timestamps. Certificate-only tasks cannot overwrite response measurements.
Deploy Controller before Scanner, then run ports/exclusive/all to populate values;
no database migration is needed.
