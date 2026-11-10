# DNS inventory

## Forward zones

The scanner imports complete AXFR snapshots of configured zones. Only A, AAAA
and CNAME records are retained; SOA supplies the serial. Zones use the natural
key (Site, DNS server, port, name), not the scanner identity. Different scanners
can collect the same source without creating a duplicate zone.

The Controller authenticates the scanner's Site, validates every record and
publishes the snapshot transactionally. It rejects out-of-zone owners, duplicate
values, conflicting CNAME/address records and stale concurrent revisions.
Retained identities preserve first_seen; removed records disappear; a failed or
partial transfer preserves the last successful snapshot.

## DNS access from Docker and Kubernetes

Authorize the scanner source addresses as seen by the DNS server: Docker container
subnets, Kubernetes Pod subnets, or node/NAT addresses, depending on networking.
Check the DNS server logs to identify the actual source; it may differ from the
host IP. Allow only the required addresses or subnets.

For BIND, permit queries with `allow-query` for the relevant forward and reverse
zones, and AXFR with `allow-transfer` for imported zones. If PTR queries require
recursion, also review `allow-recursion` and `allow-query-cache`. Check any matching
`view` and reload the configuration after changes. `REFUSED` means the DNS server
rejected the request; verify its active access rules before retrying.