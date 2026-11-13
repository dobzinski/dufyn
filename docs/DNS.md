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

## Asset hostnames and DHCP

Asset reconciliation reuses the Site's stored DNS/PTR inventory for sources
enabled in the scanner configuration, even when their collection macros are not
selected. It does not issue additional DNS queries.
A unique A name for the observed IP takes priority. A unique PTR name can be used
without an A record, unless a stored A assigns that PTR name to another address.
Ambiguous names are logged; presence is still recorded without guessing a name.

`display_name` (Name) is the presentation label; `discovered_hostname` (Hostname)
is populated automatically. Manual asset creation leaves Hostname empty. A later
asset run with DNS/PTR enabled can fill it without replacing Name.

With Network DHCP = No, assets are matched by Site/IP. Empty hostnames can be
filled, but a different discovered name does not replace an existing hostname.
DHCP = Yes first matches a unique MAC, then a unique hostname, to update the IP while preserving the
asset ID and topology. An old occupant is retained with its last known IP when
its current address is released. No asset is automatically deleted.

Without a matching MAC, a new PTR name alone does not replace a different registered hostname at the
same IP when no matching asset exists. This avoids duplicates from changing PTR
sources, but also means a real replacement may require manual review. Without a
usable hostname, matching falls back to IP and cannot reliably distinguish DHCP
address reuse. A unique NetBIOS MAC takes precedence for DHCP identity matching; DNS/PTR determine
the preferred hostname. Without MAC, ambiguous names still require review.

A failed DNS import preserves the last successful snapshot. An unavailable DNS
server therefore does not mean the stored A records are empty. An IP lookup such
as `nslookup <IP> <server>` queries PTR, not A; use
`nslookup -type=A <hostname> <server>` to check forward resolution.
See [Scanner execution](SCANS.md#asset-reconciliation-and-netbios) for cycle behavior
and optional NetBIOS collection.

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
