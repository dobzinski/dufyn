import argparse
import concurrent.futures
from contextlib import contextmanager, nullcontext
from ipaddress import ip_network
import json
import logging
import math
import re
import signal
import ssl
import socket
import struct
import secrets
import errno
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import NameOID
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler, HTTPSHandler

from app.config import configuration_snapshot, load_config, load_version, quic_port_enabled
from app.controller_tls import request_tls_options
from app.nmap_udp import scan_udp
from app.dns_sync import run_dns_cycle
from app.scan_selection import SCAN_ORDER
from app.ptr_scan import check_network_ptr
from app.quic_cert import discover_quic_certificate, check_http3

log = logging.getLogger("scanner")


class ApiError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"API returned HTTP {status}")


def api_connection_failure(error):
    cause = error.reason if isinstance(error, URLError) else error
    if isinstance(cause, socket.gaierror):
        reason = "DNS lookup failed for API hostname"
    elif isinstance(cause, ConnectionRefusedError) or getattr(cause, "errno", None) in (errno.ECONNREFUSED, 10061):
        reason = "connection refused"
    elif isinstance(cause, ssl.SSLCertVerificationError):
        reason = "TLS certificate verification failed; check controller.ca_file and certificate hostname"
    elif isinstance(cause, (TimeoutError, socket.timeout)):
        reason = "request timed out"
    else:
        reason = "network error (" + type(cause).__name__ + ")"
    return RuntimeError("API connection failed: " + reason)


class ApiClient:
    def __init__(self, config):
        self.url = config["controller"]["url"].rstrip("/")
        self.token = config["controller"]["token"]
        self.timeout = config["execution"]["request_timeout_seconds"]
        self.tls_options = request_tls_options(self.url, config["controller"].get("ca_file"))

    def post(self, path, body):
        request = Request(self.url + "/v1/private/" + path, data=json.dumps(body).encode(),
                          headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout, **self.tls_options) as response:
                return json.load(response)
        except HTTPError as error:
            status = error.code
            error.close()
            raise ApiError(status) from None
        except (URLError, TimeoutError, OSError) as error:
            raise api_connection_failure(error) from None


    def get(self, path):
        request = Request(self.url + "/v1/private/" + path,
                          headers={"Authorization": "Bearer " + self.token})
        try:
            with urlopen(request, timeout=self.timeout, **self.tls_options) as response:
                return json.load(response)
        except HTTPError as error:
            status = error.code
            error.close()
            raise ApiError(status) from None
        except (URLError, TimeoutError, OSError) as error:
            raise api_connection_failure(error) from None


    def sync_configuration(self, config):
        return self.post("scanner/config", {"config": configuration_snapshot(config)})


    def wait_ready(self, stop, attempts=12, interval=5):
        # Retry only a read-only readiness probe, never an arbitrary POST.
        for attempt in range(attempts):
            if stop.is_set():
                raise RuntimeError("Scanner interrupted while waiting for API")
            try:
                with urlopen(self.url + "/ready", timeout=self.timeout, **self.tls_options) as response:
                    if json.load(response).get("status") == "ready":
                        return
                    raise RuntimeError("API readiness response was not ready")
            except HTTPError as error:
                status = error.code
                error.close()
                if status < 500 and status != 429:
                    raise ApiError(status) from None
                reason = "API readiness returned HTTP " + str(status)
            except (URLError, TimeoutError, OSError) as error:
                reason = str(api_connection_failure(error))
            except RuntimeError as error:
                reason = str(error)
            log.warning("Waiting for API readiness: attempt=%s/%s reason=%s", attempt+1, attempts, reason)
            if attempt+1 == attempts:
                raise RuntimeError("API not ready after startup retries: " + reason)
            if stop.wait(interval):
                raise RuntimeError("Scanner interrupted while waiting for API")


class RateLimiter:
    def __init__(self, rate):
        self.interval = 1 / rate
        self.lock = threading.Lock()
        self.next_send = 0.0

    def acquire(self, stop):
        with self.lock:
            delay = max(0, self.next_send - time.monotonic())
            if stop.wait(delay):
                raise RuntimeError("Discovery interrupted")
            self.next_send = time.monotonic() + self.interval


def ping(ip, timeout, limiter, stop):
    limiter.acquire(stop)
    if stop.is_set():
        raise RuntimeError("Discovery interrupted")
    try:
        result = subprocess.run(["ping", "-n", "-c", "1", "-W", str(timeout), "-w", str(math.ceil(timeout) + 1), str(ip)],
                                capture_output=True, text=True, timeout=timeout + 3)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Ping process failed to terminate normally") from None
    if result.returncode == 1:
        return None
    if result.returncode != 0:
        raise RuntimeError("Ping execution failed; check ICMP capability and routing")
    match = re.search(r"time[=<]([0-9.]+)\s*ms", result.stdout)
    return {"ip": str(ip), "latency_ms": float(match.group(1)) if match else None}


def discover(cidr, settings, stop):
    network = ip_network(cidr)
    if network.version != 4:
        raise ValueError("Initial discovery supports IPv4 only")
    limiter = settings.get("_limiter") or RateLimiter(settings["packets_per_second"])
    recheck = settings.get("recheck", {"enabled": True, "timeout_seconds": 1})
    hosts = []
    pending = {}
    pool_context = nullcontext(settings["_pool"]) if "_pool" in settings else concurrent.futures.ThreadPoolExecutor(max_workers=settings["concurrency"])
    with pool_context as pool:
        try:
            def probe_host(ip):
                host = ping(ip, settings["ping_timeout_seconds"], limiter, stop)
                if host is None and recheck["enabled"]:
                    host = ping(ip, recheck["timeout_seconds"], limiter, stop)
                return host

            ips = iter(network.hosts())
            def submit_next():
                ip = next(ips, None)
                if ip is not None and not stop.is_set():
                    pending[pool.submit(probe_host, ip)] = ip
            for _ in range(settings["concurrency"]):
                submit_next()
            while pending:
                if stop.is_set():
                    raise RuntimeError("Discovery interrupted")
                done, _ = concurrent.futures.wait(pending, timeout=0.2, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    pending.pop(future)
                    host = future.result()
                    if host is not None:
                        hosts.append(host)
                    submit_next()
        except BaseException:
            stop.set()
            for future in pending:
                future.cancel()
            concurrent.futures.wait(pending)
            raise
    if stop.is_set():
        raise RuntimeError("Discovery interrupted")
    return hosts


class NoTargetRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def check_target(fqdn, stop, limiter, timeout=5, scheme=None, port=None):
    # Measure service reachability even with expired/self-signed certificates.
    # Certificate validity is a separate inventory check, not HTTP availability.
    opener = build_opener(NoTargetRedirect(), HTTPSHandler(context=ssl._create_unverified_context()))
    for scheme in ((scheme,) if scheme else ("https", "http")):
        limiter.acquire(stop)
        if stop.is_set():
            raise RuntimeError("Target check interrupted")
        start = time.monotonic()
        try:
            response = opener.open(Request(f"{scheme}://{fqdn}{':' + str(port) if port else ''}/", headers={"User-Agent": "NetworkDiscovery/0.1"}), timeout=timeout)
        except HTTPError as error:
            status = error.code
            error.close()
        except (URLError, TimeoutError, OSError) as error:
            cause = error.reason if isinstance(error, URLError) else error
            if isinstance(cause, OSError) and cause.errno == errno.ECONNREFUSED:
                return {"state": "C", "http_status": None, "response_time_ms": None}
            continue
        else:
            status = response.status
            response.close()
        return {"state": "O", "http_status": status, "response_time_ms": round((time.monotonic() - start) * 1000, 3)}
    return {"state": "U", "http_status": None, "response_time_ms": None}


def discover_target_certificate(fqdn, port, stop, limiter, connect_host=None):
    limiter.acquire(stop)
    if stop.is_set():
        raise RuntimeError("Target discovery interrupted")
    context = ssl._create_unverified_context()
    try:
        with socket.create_connection((connect_host or fqdn, port), timeout=5) as connection:
            with context.wrap_socket(connection, server_hostname=fqdn) as tls:
                der = tls.getpeercert(binary_form=True)
                version = tls.version()
                cipher = tls.cipher()[0]
    except (OSError, ssl.SSLError):
        return None
    cert = x509.load_der_x509_certificate(der)
    def common_name(name):
        values = name.get_attributes_for_oid(NameOID.COMMON_NAME)
        return values[0].value if values else None
    try:
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        sans = []
    return {"subject_cn": common_name(cert.subject), "issuer_cn": common_name(cert.issuer),
            "serial_number": str(cert.serial_number), "sans": sans,
            "valid_from": cert.not_valid_before_utc.isoformat(), "valid_until": cert.not_valid_after_utc.isoformat(),
            "fingerprint_sha256": cert.fingerprint(hashes.SHA256()).hex(),
            "tls_version": version, "cipher": cipher}


def check_target_identity(task, config, stop):
    settings = config["discovery"]
    limiter = settings["_limiter"]
    status = task["status"]
    if status == "I":
        log.info("Target skipped: fqdn=%s status=Inactive",task["fqdn"])
        return {"status":"I", "latency_ms":None, "ports":[]}
    try:
        ip = socket.getaddrinfo(task["fqdn"], None, socket.AF_INET)[0][4][0]
    except socket.gaierror:
        ip = None
    reply = ping(ip, settings.get("ping_timeout_seconds", 1), limiter, stop) if ip else None
    if ip and reply is None and settings.get("recheck", {"enabled": True})["enabled"]:
        reply = ping(ip, settings.get("recheck", {"timeout_seconds": 1})["timeout_seconds"], limiter, stop)
    latency = reply.get("latency_ms") if reply is not None else None
    status = "A" if reply is not None else "U" if status == "U" else "I"
    return {"status": status, "latency_ms": latency, "ports": []}


def check_target_ports(task, config, stop):
    status = task["status"]
    limiter = config["discovery"]["_limiter"]
    latency = None
    results = []
    if status != "I":
        for port in task["ports"]:
            if stop.is_set():
                raise RuntimeError("Target check interrupted")
            if port.get("protocol", "T") == "U":
                if quic_port_enabled(config, port["port"]):
                    limiter.acquire(stop)
                    outcome=check_http3(task["fqdn"],port["port"],stop,config.get("udp",{}).get("quic",{}).get("timeout_seconds",5))
                else:
                    # Preserve the configured result shape without probing or changing inventory.
                    outcome={"state":"U", "http_status":None, "response_time_ms":None,
                             "check_protocol":"https", "certificate":None}
                results.append({"id":port["id"],**outcome})
                continue
            certificate = discover_target_certificate(task["fqdn"], port["port"], stop, limiter)
            scheme = "https" if certificate is not None else "http"
            outcome = check_target(task["fqdn"], stop, limiter, scheme=scheme, port=port["port"])
            outcome.update(check_protocol=scheme, certificate=certificate)
            results.append({"id": port["id"], **outcome})
    else:
        log.info("Target ports skipped: fqdn=%s status=Inactive", task["fqdn"])
    return {"status": status, "latency_ms": latency, "ports": results}


def check_target_task(task, config, stop):
    identity = check_target_identity(task, config, stop)
    if identity["status"] == "I":
        return identity
    outcome = check_target_ports({**task, "status": identity["status"]}, config, stop)
    return {**outcome, "status": identity["status"], "latency_ms": identity["latency_ms"]}


def check_certificate_ports(task, config, stop, target=False):
    limiter = config["discovery"]["_limiter"]
    host = task["fqdn"] if target else task["ip"]
    sni = task["fqdn"] if target else task.get("discovered_hostname") or host
    results = []
    for port in task["ports"]:
        if stop.is_set():
            raise RuntimeError("Certificate scan interrupted")
        certificate = None
        if port["protocol"] == "T":
            certificate = discover_target_certificate(sni, port["port"], stop, limiter, connect_host=host)
        elif quic_port_enabled(config, port["port"]):
            limiter.acquire(stop)
            certificate = discover_quic_certificate(host, port["port"], sni, stop,
                config.get("udp", {}).get("quic", {}).get("timeout_seconds", 5))
        observation = {"id":port["id"], "state":"O" if certificate is not None else "U", "certificate":certificate}
        if target:
            observation["check_protocol"] = "https"
        elif certificate is not None and port["protocol"] == "U":
            observation["certificate_protocol"] = "quic"
        results.append(observation)
    if target:
        return {"status":task["status"], "latency_ms":None, "ports":results}
    return {"ports":results}


def check_udp(ip, port, stop, limiter, *, measure=False):
    def outcome(state, elapsed=None):
        return {"state": state, "response_time_ms": elapsed} if measure else state

    if port == 53:
        query_id = secrets.randbelow(65536)
        # Query root NS; a REFUSED/SERVFAIL DNS response still proves reachability.
        payload = struct.pack("!HHHHHH",query_id,0x0100,1,0,0,0) + b"\x00\x00\x02\x00\x01"
        def valid(response):
            return len(response)>=12 and struct.unpack("!H",response[:2])[0]==query_id and bool(response[2]&0x80)
    elif port == 123:
        now = time.time() + 2208988800
        seconds = int(now)
        timestamp = struct.pack("!II", seconds & 0xffffffff, int((now-seconds)*2**32))
        payload = b"\x23" + bytes(39) + timestamp
        def valid(response):
            return len(response)>=48 and response[0]&7 == 4 and (response[0]>>3)&7 in (3,4) and response[24:32] == timestamp
    else:
        log.info("UDP check unsupported: ip=%s port=%s; leaving Unknown",ip,port)
        return outcome("U")
    limiter.acquire(stop)
    if stop.is_set():
        raise RuntimeError("UDP scan interrupted")
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as connection:
            connection.settimeout(2)
            connection.connect((ip,port))
            started = time.monotonic()
            connection.send(payload)
            response=connection.recv(4096)
            elapsed = round((time.monotonic() - started) * 1000, 3)
        state = "O" if valid(response) else "U"
        log.info("UDP check: ip=%s port=%s state=%s reason=%s",ip,port,state,"valid service reply" if state=="O" else "unrecognized reply")
        return outcome(state, elapsed if state == "O" else None)
    except ConnectionRefusedError:
        log.info("UDP check: ip=%s port=%s state=C reason=connection refused",ip,port)
        return outcome("C")
    except OSError as error:
        state = "C" if error.errno == errno.ECONNREFUSED else "U"
        reason = "timeout/no reply" if isinstance(error,TimeoutError) else "connection refused" if state=="C" else f"network error errno={error.errno}"
        log.info("UDP check: ip=%s port=%s state=%s reason=%s",ip,port,state,reason)
        return outcome(state)


def check_asset_ports(task, config, stop):
    limiter = config["discovery"]["_limiter"]
    results=[]
    udp_ports=[port["port"] for port in task["ports"] if port["protocol"]=="U"]
    udp=scan_udp(task["ip"],udp_ports,config,stop,config["discovery"].get("_udp_lock") or threading.Lock()) if udp_ports else {}
    for port in task["ports"]:
        if stop.is_set():
            raise RuntimeError("Asset port scan interrupted")
        if port["protocol"] == "U":
            observation={"id":port["id"],**udp[port["port"]],"certificate":None,"response_time_ms":None}
            if observation["state"] == "O" and observation["checked"] and port["port"] in (53, 123):
                timing = check_udp(task["ip"], port["port"], stop, limiter, measure=True)
                observation["response_time_ms"] = timing["response_time_ms"]
            options=config.get("udp",{}).get("quic",{})
            if quic_port_enabled(config, port["port"]) and observation["checked"] and observation["state"] != "C":
                limiter.acquire(stop)
                started = time.monotonic()
                certificate=discover_quic_certificate(task["ip"],port["port"],task.get("discovered_hostname") or task["ip"],stop,options.get("timeout_seconds",5))
                if certificate is not None:
                    observation.update(state="O",certificate=certificate,certificate_protocol="quic",
                        response_time_ms=round((time.monotonic() - started) * 1000, 3))
            results.append(observation)
            continue
        limiter.acquire(stop)
        if stop.is_set():
            raise RuntimeError("Asset port scan interrupted")
        response_time_ms = None
        started = time.monotonic()
        try:
            with socket.create_connection((task["ip"],port["port"]),timeout=2):
                response_time_ms = round((time.monotonic() - started) * 1000, 3)
            state="O"
        except OSError as error:
            response_time_ms = None
            state="C" if error.errno == errno.ECONNREFUSED else "U"
        certificate=None
        if state=="O" and config.get("_scan_scope", "full") != "ports":
            certificate=discover_target_certificate(task.get("discovered_hostname") or task["ip"],port["port"],stop,limiter,connect_host=task["ip"])
        results.append({"id":port["id"],"state":state,"certificate":certificate,"response_time_ms":response_time_ms})
    return {"ports":results}


STAGES = {
    "discovery": ("discovery", "excluded_network_ids", "network_id", None),
    "targets": ("targets", "excluded_target_ids", "target_id", None),
    "target-hosts": ("targets", "excluded_target_ids", "target_id", "targets"),
    "target-ports": ("targets", "excluded_target_ids", "target_id", "ports"),
    "target-exclusive": ("targets", "excluded_target_ids", "target_id", "exclusive"),
    "asset-exclusive": ("asset-ports", "excluded_asset_ids", "asset_id", "exclusive"),
    "target-certificates": ("targets", "excluded_target_ids", "target_id", "certificates"),
    "asset-ports": ("asset-ports", "excluded_asset_ids", "asset_id", "ports"),
    "asset-certificates": ("asset-ports", "excluded_asset_ids", "asset_id", "certificates"),
    "ptr": ("discovery", "excluded_network_ids", "network_id", "ptr"),
}


def execute_task(client, task, config, stop, stage="discovery"):
    worker = {"worker_id": config["worker_id"]}
    route, _, _, requested_scope = STAGES[stage]
    scope = task.get("scope", requested_scope or "full")
    config = {**config, "_scan_scope": scope}
    path = f"{route}/{task['id']}"
    client.post(path + "/heartbeat", worker)
    heartbeat_done = threading.Event()
    heartbeat_errors = []

    def keep_lease():
        while not heartbeat_done.wait(config["execution"]["heartbeat_interval_seconds"]):
            if stop.is_set():
                return
            try:
                client.post(path + "/heartbeat", worker)
            except Exception as error:
                heartbeat_errors.append(error)
                stop.set()
                return

    thread = threading.Thread(target=keep_lease, name="heartbeat", daemon=True)
    thread.start()
    try:
        if stage == "discovery":
            log.info("Discovery started: task=%s network=%s", task["id"], task["cidr"])
            hosts = discover(task["cidr"], config["discovery"], stop)
            body = {**worker, "complete": True, "hosts": hosts}
        elif stage == "ptr":
            log.info("Network PTR started: task=%s network=%s", task["id"], task["cidr"])
            body = {**worker, "complete": True, "scope": "ptr", "hosts": [],
                    "ptr": check_network_ptr(task["cidr"], config, stop)}
        elif route == "asset-ports":
            log.info("Asset %s started: task=%s ip=%s", scope, task["id"], task["ip"])
            if scope == "certificates":
                body = {**worker, **check_certificate_ports(task,config,stop)}
            else:
                body = {**worker, **check_asset_ports(task,config,stop)}
        else:
            log.info("Target %s started: task=%s fqdn=%s", scope, task["id"], task["fqdn"])
            checker = {"targets":check_target_identity, "ports":check_target_ports, "exclusive":check_target_ports}.get(scope)
            outcome = check_certificate_ports(task,config,stop,target=True) if scope == "certificates" else (checker or check_target_task)(task,config,stop)
            body = {**worker, **outcome}
        for attempt in range(config["execution"]["result_attempts"]):
            if stop.is_set():
                raise RuntimeError("Discovery interrupted; result not submitted")
            try:
                result = client.post(path + "/result", body)
                if result.get("state") != "completed":
                    raise RuntimeError("API did not acknowledge completion")
                if stage == "discovery":
                    log.info("Discovery completed: task=%s responding_hosts=%s", task["id"], len(hosts))
                elif stage == "ptr":
                    observations = body["ptr"]["observations"]
                    log.info("Network PTR completed: network=%s addresses=%s answered=%s", task["cidr"],
                             len(observations), sum(item["result"] == "answered" for item in observations))
                elif route == "asset-ports":
                    log.info("Asset %s completed: ip=%s ports=%s",scope,task["ip"],len(body["ports"]))
                else:
                    log.info("Target check completed: fqdn=%s status=%s ports=%s", task["fqdn"], body["status"], len(body["ports"]))
                return result
            except ApiError as error:
                if error.status < 500 and error.status != 429:
                    raise
            except RuntimeError:
                pass
            if attempt + 1 == config["execution"]["result_attempts"]:
                raise RuntimeError("Result delivery failed; no new task will start")
            if stop.wait(config["execution"]["retry_interval_seconds"]):
                raise RuntimeError("Discovery interrupted")
    finally:
        heartbeat_done.set()
        thread.join(timeout=config["execution"]["request_timeout_seconds"] + 1)
        if heartbeat_errors:
            log.error("Task heartbeat failed")


@contextmanager
def execution_resources(config):
    """Reuse probe workers across stages; Discovery coordinators must be separate."""
    settings = config.get("discovery")
    if settings is None:
        yield config
        return
    parallel = min(settings["parallel_networks"], settings["concurrency"])
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=settings["concurrency"], thread_name_prefix="probe") as probes:
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=parallel, thread_name_prefix="network") as coordinators:
            yield {**config, "discovery": {
                **settings, "_pool": probes, "_coordinator_pool": coordinators,
            }}


def _run_cycle(client, config, stop, claimed, stage="discovery"):
    # One global ping pool/rate limiter, independent of the number of networks.
    parallel_tasks = (
        min(config["discovery"]["parallel_networks"], config["discovery"]["concurrency"])
        if stage in ("discovery", "ptr") else config["discovery"]["concurrency"]
    )
    route, excluded_key, identity_key, scope = STAGES[stage]
    excluded = set()
    active = set()
    settings = config["discovery"]
    pool_context = nullcontext(settings["_pool"]) if "_pool" in settings else concurrent.futures.ThreadPoolExecutor(max_workers=settings["concurrency"])
    with pool_context as ping_pool:
        runtime_config = {**config, "discovery": {
            **config["discovery"], "_pool": ping_pool,
            "_limiter": RateLimiter(config["discovery"]["packets_per_second"]/(2 if stage=="asset-ports" else 1)),
            "_udp_lock": threading.Lock(),
        }}
        if "_coordinator_pool" in settings:
            executor = settings["_coordinator_pool"] if stage in ("discovery", "ptr") else ping_pool
            task_context = nullcontext(executor)
        else:
            task_context = concurrent.futures.ThreadPoolExecutor(max_workers=parallel_tasks)
        with task_context as networks:
            try:
                while not stop.is_set():
                    for future in list(active):
                        if future.done():
                            active.remove(future)
                            future.result()
                    available = True
                    while len(active) < parallel_tasks and not stop.is_set():
                        # Check failures before claiming more work.
                        for future in active:
                            if future.done():
                                future.result()
                        claim = {"worker_id": config["worker_id"], excluded_key: sorted(excluded)}
                        if scope is not None:
                            claim["scope"] = scope
                        allocation = client.post(f"{route}/claim", claim)
                        task = allocation.get("task")
                        if task is None:
                            if stage == "discovery" and not claimed and "availability" in allocation:
                                assigned = allocation["availability"]
                                if not assigned:
                                    log.warning("No networks assigned to this scanner in its site")
                                for network in assigned:
                                    reason = "reserved by an active task" if network["reserved"] else "disabled" if network["enabled"] != "Y" else "IPv6 is unsupported" if network["address_family"] != 4 else "excluded in this cycle" if network["excluded_in_cycle"] else "temporarily locked by another transaction; retry"
                                    log.warning("Discovery unavailable: network=%s reason=%s", network["cidr"], reason)
                            available = False
                            break
                        claimed.append(task)
                        excluded.add(task[identity_key])
                        active.add(networks.submit(execute_task, client, task, runtime_config, stop) if stage == "discovery" else networks.submit(execute_task, client, task, runtime_config, stop, stage))
                    if not active and not available:
                        log.info("%s cycle completed: tasks=%s; no more available tasks", stage, len(claimed))
                        return
                    if active:
                        concurrent.futures.wait(active, timeout=0.2, return_when=concurrent.futures.FIRST_COMPLETED)
            except BaseException:
                stop.set()
                for future in active:
                    future.cancel()
                concurrent.futures.wait(active)
                raise


def run_cycle(client, config, stop, stage="discovery"):
    claimed = []
    failed = False
    try:
        _run_cycle(client, config, stop, claimed) if stage == "discovery" else _run_cycle(client, config, stop, claimed, stage)
    except BaseException:
        failed = True
        raise
    finally:
        # _run_cycle drains task/probe futures first: no local probe/heartbeat remains.
        # Release by task UUID and worker identity; never affect another worker.
        if failed or stop.is_set():
            for task in claimed:
                try:
                    result = client.post(f"{STAGES[stage][0]}/{task['id']}/cancel", {"worker_id": config["worker_id"]})
                    if result.get("released"):
                        log.info("Discovery reservation released: task=%s", task["id"])
                except Exception:
                    log.warning("Unable to release task=%s; reservation will expire without heartbeat", task["id"])


def scan_assets(client, config, stop):
    run_cycle(client, config, stop)


def scan_targets(client, config, stop):
    if not config.get("targets", {}).get("enabled", True):
        log.info("Target checks disabled by targets.enabled")
        return
    run_cycle(client, config, stop, "target-hosts")


def scan_ports(client, config, stop):
    run_cycle(client, config, stop, "asset-ports")
    if not stop.is_set() and config.get("targets", {}).get("enabled", True):
        run_cycle(client, config, stop, "target-ports")


def scan_exclusive(client, config, stop):
    run_cycle(client, config, stop, "asset-exclusive")
    if not stop.is_set() and config.get("targets", {}).get("enabled", True):
        run_cycle(client, config, stop, "target-exclusive")


def scan_certificates(client, config, stop):
    run_cycle(client, config, stop, "asset-certificates")
    if not stop.is_set() and config.get("targets", {}).get("enabled", True):
        run_cycle(client, config, stop, "target-certificates")


def scan_dns(client, config, stop):
    if not config.get("dns", {}).get("enabled", False):
        return True
    dns_client = ApiClient({**config, "execution": {
        **config["execution"], "request_timeout_seconds":config["dns"]["api_timeout_seconds"],
    }})
    return run_dns_cycle(dns_client, config, stop)


def scan_ptr(client, config, stop):
    if not config.get("ptr", {}).get("enabled", False):
        return
    run_cycle(client, config, stop, "ptr")


def run(config, stop):
    client = ApiClient(config)
    selected = config["execution"].get("scans", list(SCAN_ORDER))
    explicit = config["execution"].get("_scans_explicit", False)
    functions = {"assets":scan_assets, "targets":scan_targets, "ports":scan_ports,
                 "exclusive":scan_exclusive, "certificates":scan_certificates, "dns":scan_dns, "ptr":scan_ptr}
    next_cycle_at = time.monotonic()
    while not stop.is_set():
        try:
            client.sync_configuration(config)
        except Exception as error:
            # Never log settings/credentials or block discovery on a backup failure.
            status = f"HTTP {error.status}" if isinstance(error, ApiError) else type(error).__name__
            log.warning("Scanner configuration sync failed (%s); retrying next cycle", status)
        current = list(selected)
        if not explicit and config.get("dns", {}).get("enabled", False):
            if not client.get("config")["networks"]:
                current = [name for name in current if name in ("dns", "ptr")]
                log.info("DNS-only cycle: no enabled networks assigned")
        dns_success = True
        log.info("Scanner macros: %s", ", ".join(current))
        with execution_resources(config) as cycle_config:
            for name in SCAN_ORDER:
                if stop.is_set():
                    break
                if name not in current:
                    continue
                result = functions[name](client, cycle_config, stop)
                if name == "dns":
                    dns_success = result
        if config["execution"]["mode"] == "once":
            if not dns_success and not stop.is_set():
                raise RuntimeError("One or more DNS zones failed to synchronize")
            return
        if stop.is_set():
            return
        interval = config["execution"]["interval_seconds"]
        next_cycle_at += interval
        now = time.monotonic()
        if next_cycle_at < now:
            missed = int((now - next_cycle_at) // interval) + 1
            next_cycle_at += missed * interval
            log.warning("Scanner cycle exceeded scheduled start; skipped_slots=%s", missed)
        remaining = max(0.0, next_cycle_at - now)
        log.info("Scanner cycle completed; next cycle in %.1f seconds", remaining)
        if stop.wait(remaining):
            return


def main():
    parser = argparse.ArgumentParser(description="Run macro scans for this scanner's assigned scope")
    parser.add_argument("--scans", nargs="+", help="all or macro names: assets, targets, ports, exclusive, certificates, dns, ptr")
    parser.add_argument("--mode", choices=("once","continuous"), help="Override execution mode")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    try:
        config = load_config(scans_override=args.scans, mode_override=args.mode)
        log.info("Scanner version=%s mode=%s worker=%s", load_version(), config["execution"]["mode"], config["worker_id"])
        ApiClient(config).wait_ready(stop)
        run(config, stop)
        return 1 if stop.is_set() else 0
    except Exception as error:
        log.error("Scanner stopped: %s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
