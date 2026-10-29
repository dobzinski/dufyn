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