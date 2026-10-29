"""PTR observations for Assets and entire assigned networks."""
from ipaddress import ip_address

import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.query
import dns.rcode
import dns.rdatatype
import dns.resolver
import dns.reversename

from app.dns_sync import normalize_name


def valid_ptr_domain(value):
    import re
    try:
        name = normalize_name(value)
        if len(name.split('.')) < 2 or any(not re.fullmatch(
            r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in name.split('.')):
            return None
        try:
            ip_address(name)
            return None
        except ValueError:
            return name
    except (ValueError, dns.exception.DNSException):
        return None


def ptr_server(config):
    settings = config.get("dns", {})
    if settings.get("server"):
        return settings["server"], settings.get("port", 53)
    resolver = dns.resolver.Resolver()
    for server in resolver.nameservers:
        if isinstance(server, str):
            try:
                return str(ip_address(server)), resolver.port
            except ValueError:
                continue
    raise ValueError("No DNS resolver available for PTR; configure dns.server")


def check_ptr(task, config, stop):
    settings = config.get("dns", {})
    timeout = settings.get("timeout_seconds", 5)
    limiter = config.get("discovery", {}).get("_limiter")
    current = dns.reversename.from_address(task["ip"])
    visited = set()
    try:
        for _ in range(16):
            if stop.is_set():
                raise RuntimeError("PTR check interrupted")
            if current in visited:
                return {"result":"error", "error_message":"Reverse CNAME loop", "records":[]}
            visited.add(current)
            query = dns.message.make_query(current, dns.rdatatype.PTR)
            if limiter is not None:
                limiter.acquire(stop)
            response = dns.query.udp(query, task["dns_server"], port=task["dns_port"], timeout=timeout)
            if response.flags & dns.flags.TC:
                if limiter is not None:
                    limiter.acquire(stop)
                response = dns.query.tcp(query, task["dns_server"], port=task["dns_port"], timeout=timeout)
            if stop.is_set():
                raise RuntimeError("PTR check interrupted")
            code = response.rcode()
            if code != 0:
                kind = {3:"nxdomain", 2:"servfail", 5:"refused"}.get(code, "error")
                return {"result":kind, "response_code":code, "records":[]}
            # Handle classless reverse delegations: CNAME and PTR may share a response.
            while True:
                answers = [rrset for rrset in response.answer if rrset.name == current]
                ptrs = [rrset for rrset in answers if rrset.rdtype == dns.rdatatype.PTR]
                if ptrs:
                    values = {}
                    for rrset in ptrs:
                        for item in rrset:
                            name = valid_ptr_domain(item.target.to_text())
                            if name is not None:
                                values[name] = rrset.ttl
                    if not values:
                        return {"result":"no_ptr", "response_code":0, "records":[]}
                    if len(values) > 128:
                        return {"result":"error", "error_message":"Too many PTR answers", "records":[]}
                    return {"result":"answered", "response_code":0,
                            "records":[{"hostname":name,"ttl":ttl} for name,ttl in sorted(values.items())]}
                aliases = [rrset for rrset in answers if rrset.rdtype == dns.rdatatype.CNAME]
                if not aliases:
                    return {"result":"no_ptr", "response_code":0, "records":[]}
                current = aliases[0][0].target
                if not any(rrset.name == current for rrset in response.answer):
                    break
                if current in visited:
                    return {"result":"error","error_message":"Reverse CNAME loop","records":[]}
                visited.add(current)
        return {"result":"error", "error_message":"Reverse CNAME chain limit", "records":[]}
    except dns.exception.Timeout:
        return {"result":"timeout", "records":[]}
    except (OSError, dns.exception.DNSException, ValueError):
        return {"result":"error", "error_message":"DNS reverse query failed", "records":[]}


def check_network_ptr(cidr, config, stop):
    """Sweep all usable addresses through the container resolver, never gated by ping."""
    import concurrent.futures
    from contextlib import nullcontext
    from ipaddress import ip_network

    settings = config["discovery"]
    server, port = ptr_server({})  # Local resolv.conf, independent of AXFR dns.server.
    ips = iter(ip_network(cidr).hosts())
    pending = set()
    observations = []
    pool_context = nullcontext(settings["_pool"]) if "_pool" in settings else concurrent.futures.ThreadPoolExecutor(max_workers=settings["concurrency"])
    with pool_context as pool:
        def probe(ip):
            if stop.is_set():
                raise RuntimeError("Network PTR scan interrupted")
            task = {"ip": str(ip), "dns_server": server, "dns_port": port}
            return {"ip": str(ip), **check_ptr(task, config, stop)}

        def submit_next():
            ip = next(ips, None)
            if ip is not None and not stop.is_set():
                pending.add(pool.submit(probe, ip))
        try:
            for _ in range(settings["concurrency"]):
                submit_next()
            while pending:
                if stop.is_set():
                    raise RuntimeError("Network PTR scan interrupted")
                done, _ = concurrent.futures.wait(pending, timeout=0.2,
                    return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    pending.remove(future)
                    observations.append(future.result())
                    submit_next()
        except BaseException:
            stop.set()
            for future in pending:
                future.cancel()
            concurrent.futures.wait(pending)
            raise
    if stop.is_set():
        raise RuntimeError("Network PTR scan interrupted")
    return {"dns_server": server, "dns_port": port, "observations": observations}
