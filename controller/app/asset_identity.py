"""Reconcile discovery observations using only the existing DNS/PTR inventory."""
from collections import Counter
from datetime import datetime, timezone
import logging
from types import SimpleNamespace

log = logging.getLogger("controller")


def hostname_key(value):
    return value.strip().lower().rstrip('.') if value and value.strip() else None


def observation_time(value):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value,str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def stored_identities(connection, site_id, hosts, *, dns_enabled=True, ptr_enabled=True):
    if not hosts:
        return []
    ips = [str(host.ip) for host in hosts]
    reverse = connection.execute("""SELECT host(p.ip) AS ip,p.hostname
        FROM dns_ptr_record p JOIN dns_reverse_check c ON c.id=p.reverse_check_id
            AND c.site_id=p.site_id AND c.ip=p.ip
        WHERE p.site_id=%s AND p.ip=ANY(%s::inet[]) AND c.result='answered'""",
        (site_id,ips)).fetchall() if ptr_enabled else []
    names = sorted({row['hostname'] for row in reverse})
    forward = connection.execute("""SELECT r.name,host(r.address) AS ip
        FROM dns_record r JOIN dns_zone z ON z.id=r.zone_id
        WHERE z.site_id=%s AND z.last_synced_at IS NOT NULL AND r.record_type='A'
            AND (r.address=ANY(%s::inet[]) OR r.name=ANY(%s::text[]))""",
        (site_id,ips,names)).fetchall() if dns_enabled else []
    reverse_names, forward_names, forward_addresses = {}, {}, {}
    for row in reverse:
        reverse_names.setdefault(row['ip'],set()).add(row['hostname'])
    for row in forward:
        forward_names.setdefault(row['ip'],set()).add(row['name'])
        forward_addresses.setdefault(row['name'],set()).add(row['ip'])
    identified = []
    for host in hosts:
        observation = SimpleNamespace(ip=host.ip,latency_ms=host.latency_ms,display_name=host.display_name,
            mac_address=getattr(host,'mac_address',None),
            observed_at=getattr(host,'observed_at',None),hostname=None,identity_status='missing',
            identity_reason=None,identity_source=None,identity_aliases=[])
        ptr_names = reverse_names.get(str(host.ip),set())
        a_names = forward_names.get(str(host.ip),set())
        if len(a_names) == 1:
            observation.hostname = next(iter(a_names))
            observation.identity_source = 'dns'
        elif len(a_names) > 1:
            matching = a_names & ptr_names
            if len(matching) == 1:
                observation.hostname = next(iter(matching))
                observation.identity_source = 'dns'
            else:
                observation.identity_status = 'conflict'
                observation.identity_reason = 'Multiple stored DNS A names for this address'
        elif len(ptr_names) == 1:
            candidate = next(iter(ptr_names))
            if forward_addresses.get(candidate):
                observation.identity_status = 'conflict'
                observation.identity_reason = 'Stored DNS A assigns the PTR name to another address'
            else:
                observation.hostname = candidate
                observation.identity_source = 'ptr'
        elif len(ptr_names) > 1:
            observation.identity_status = 'conflict'
            observation.identity_reason = 'Multiple stored PTR names'
        if observation.hostname:
            observation.identity_status = 'verified'
            observation.identity_aliases = sorted(name for name in ptr_names
                if not forward_addresses.get(name) or str(host.ip) in forward_addresses[name])
        identified.append(observation)
    return identified


def plan_hosts(assets, hosts, dhcp):
    assets = [dict(asset) for asset in assets]
    by_ip = {str(asset['ip']): asset for asset in assets if asset['ip'] is not None}
    by_mac = {}
    for asset in assets:
        if asset.get("mac_address"):
            by_mac.setdefault(str(asset["mac_address"]).lower(), []).append(asset)
    mac_counts = Counter(str(host.mac_address).lower() for host in hosts if getattr(host,"mac_address",None))
    by_name = {}
    for asset in assets:
        name = hostname_key(asset['discovered_hostname'])
        if name:
            by_name.setdefault(name, []).append(asset)
    counts = Counter(hostname_key(host.hostname) for host in hosts if host.identity_status == 'verified')
    plans = []
    for host in hosts:
        name = hostname_key(host.hostname) if host.identity_status == 'verified' else None
        owner = by_ip.get(str(host.ip))
        state, reason = host.identity_status, host.identity_reason
        asset = owner
        rename = False
        mac = str(host.mac_address).lower() if getattr(host,"mac_address",None) else None
        owner_mac = str(owner['mac_address']).lower() if owner and owner.get('mac_address') else None
        if dhcp and mac:
            mac_matches = by_mac.get(mac,[])
            if len(mac_matches) > 1 or mac_counts[mac] > 1:
                plans.append({'host':host,'asset':owner,'owner':owner,'name':None,'state':'conflict',
                              'reason':'MAC address is not unique in this site or scan','rename':False})
                continue
            if mac_matches:
                asset = mac_matches[0]
                rename = bool(name and hostname_key(asset['discovered_hostname']) != name)
                plans.append({'host':host,'asset':asset,'owner':owner,'name':name,'state':'verified',
                              'reason':None,'rename':rename})
                continue
            if owner and owner_mac is None and name is None:
                plans.append({'host':host,'asset':owner,'owner':owner,'name':None,'state':state,
                              'reason':reason,'rename':False})
                continue
            if owner_mac and owner_mac != mac and name is None:
                plans.append({'host':host,'asset':None,'owner':owner,'name':None,'state':'verified',
                              'reason':None,'rename':False})
                continue
        if dhcp and name:
            matches = [value for value in by_name.get(name, [])
                       if not mac or not value.get('mac_address') or str(value['mac_address']).lower() == mac]
            aliases = {hostname_key(value) for value in getattr(host,'identity_aliases',[])}
            if not matches and getattr(host,'identity_source',None) == 'dns':
                alias_matches = {value['id']: value for alias in aliases for value in by_name.get(alias,[])
                                 if not mac or not value.get('mac_address') or str(value['mac_address']).lower() == mac}
                matches = list(alias_matches.values())
            if counts[name] > 1 or len(matches) > 1:
                state, reason = 'conflict', 'Hostname is not unique in this site or scan'
            elif not matches and owner and hostname_key(owner['discovered_hostname']) not in (None,name) \
                    and getattr(host,'identity_source',None) == 'ptr' and not (mac and owner_mac and mac != owner_mac):
                state, reason = 'conflict', 'PTR differs from the registered hostname; preserving the existing asset'
            else:
                asset = matches[0] if matches else (owner if owner and not hostname_key(owner['discovered_hostname']) and not (mac and owner_mac and mac != owner_mac) else None)
                rename = bool(asset and getattr(host,'identity_source',None) == 'dns'
                    and hostname_key(asset['discovered_hostname']) in aliases
                    and hostname_key(asset['discovered_hostname']) != name)
        elif not dhcp and owner and name and hostname_key(owner['discovered_hostname']) not in (None, name):
            state, reason = 'conflict', 'Observed hostname differs from the registered hostname'
        if state == 'conflict':
            # Presence still enters inventory; ambiguity must never move another asset.
            asset, name, rename = owner, None, False
        plans.append({'host': host, 'asset': asset, 'owner': owner, 'name': name,
                      'state': state, 'reason': reason, 'rename': rename})
    return plans


def save_presence(connection, scanner, network, hosts, observed_at):
    """Publish ICMP presence immediately while hostname reconciliation is pending."""
    connection.execute('SELECT id FROM site WHERE id=%s FOR UPDATE', (scanner['site_id'],))
    connection.execute("""UPDATE asset SET status='I',latency_ms=NULL
        WHERE site_id=%s AND ip <<= %s::cidr AND status='A'
            AND NOT (ip=ANY(%s::inet[])) AND (last_seen IS NULL OR last_seen <= %s)""",
        (scanner['site_id'],network['cidr'],[str(host.ip) for host in hosts],observation_time(observed_at)))
    for host in hosts:
        seen = observation_time(host.observed_at or observed_at)
        connection.execute("""UPDATE asset SET status='A',latency_ms=%s,discovered_by_icmp='Y'
            WHERE site_id=%s AND ip=%s AND (last_seen IS NULL OR last_seen <= %s)""",
            (host.latency_ms,scanner['site_id'],str(host.ip),seen))


def save_discovery(connection, scanner, network, hosts, *, dns_enabled=False, ptr_enabled=False, observed_at=None):
    connection.execute('SELECT id FROM site WHERE id=%s FOR UPDATE', (scanner['site_id'],))
    assets = connection.execute("""SELECT id,host(ip) AS ip,discovered_hostname,last_seen,mac_address::text AS mac_address
        FROM asset WHERE site_id=%s ORDER BY id FOR UPDATE""", (scanner['site_id'],)).fetchall()
    hosts = stored_identities(connection, scanner['site_id'], hosts,
                              dns_enabled=dns_enabled,ptr_enabled=ptr_enabled)
    dhcp = network['dhcp'] == 'Y'
    plans = plan_hosts(assets, hosts, dhcp)
    plans = [item for item in plans if not any(
        asset and asset['last_seen'] and observation_time(asset['last_seen']) >
        observation_time(getattr(item['host'],'observed_at',None) or observed_at)
        for asset in (item['asset'],item['owner']))]
    connection.execute("""UPDATE asset SET status='I',latency_ms=NULL
        WHERE site_id=%s AND ip <<= %s::cidr AND status='A'
            AND (last_seen IS NULL OR last_seen <= %s)""",
        (scanner['site_id'],network['cidr'],observation_time(observed_at)))
    if dhcp:
        release = set()
        for item in plans:
            if item['state'] != 'verified':
                continue
            if item['asset'] and item['asset']['ip'] != str(item['host'].ip):
                release.add(item['asset']['id'])
            if item['owner'] and item['owner'] != item['asset']:
                release.add(item['owner']['id'])
        if release:
            connection.execute("""UPDATE asset SET last_known_ip=COALESCE(ip,last_known_ip),ip=NULL,
                status='I',latency_ms=NULL WHERE id=ANY(%s::bigint[])""", (sorted(release),))
    for item in plans:
        host, asset = item['host'], item['asset']
        seen = observation_time(getattr(host,'observed_at',None) or observed_at)
        name = item['name'] if item['state'] == 'verified' else None
        if item['state'] == 'conflict':
            log.warning('Discovery identity conflict: site=%s ip=%s reason=%s',scanner['site_id'],host.ip,item['reason'])
        if asset:
            connection.execute("""UPDATE asset SET ip=%s,last_known_ip=%s,
                discovered_hostname=CASE WHEN %s THEN %s ELSE COALESCE(NULLIF(discovered_hostname,''),%s) END,
                display_name=COALESCE(NULLIF(display_name,''),%s),
                mac_address=COALESCE(%s::macaddr,mac_address),
                discovered_by_icmp='Y',status='A',latency_ms=%s,
                first_seen=COALESCE(first_seen,%s),last_seen=%s,last_seen_active=%s WHERE id=%s
                    AND (last_seen IS NULL OR last_seen <= %s)""",
                (str(host.ip),str(host.ip),item['rename'],name,name,host.display_name,host.mac_address if item['state'] != 'conflict' or not dhcp else None,host.latency_ms,seen,seen,seen,asset['id'],seen))
        else:
            connection.execute("""INSERT INTO asset(site_id,ip,last_known_ip,discovered_hostname,display_name,mac_address,
                discovered_by_icmp,status,latency_ms,first_seen,last_seen,last_seen_active)
                VALUES (%s,%s,%s,%s,%s,%s::macaddr,'Y','A',%s,%s,%s,%s)""",
                (scanner['site_id'],str(host.ip),str(host.ip),name,host.display_name,host.mac_address,host.latency_ms,seen,seen,seen))
