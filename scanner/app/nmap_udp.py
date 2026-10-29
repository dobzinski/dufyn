"""UDP-only Nmap adapter. TCP and certificate logic stay in the Python scanner."""
from ipaddress import IPv4Address
import logging
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

log=logging.getLogger("scanner")


def parse_udp_xml(xml, ip, ports):
    root=ET.fromstring(xml)
    finished=root.find("runstats/finished")
    if finished is None or finished.get("exit") != "success":
        raise RuntimeError("Nmap did not complete successfully")
    host=next((h for h in root.findall("host") if any(a.get("addr")==ip for a in h.findall("address"))),None)
    results={port:{"state":"U","udp_scan_state":"not-scanned","checked":False} for port in ports}
    if host is None or host.get("timedout")=="true":
        return results
    for item in host.findall("ports/port"):
        if item.get("protocol")!="udp": continue
        port=int(item.get("portid"))
        if port not in results: continue
        state=item.find("state")
        raw=state.get("state","unknown") if state is not None else "unknown"
        results[port]={"state":{"open":"O","closed":"C"}.get(raw,"U"),"udp_scan_state":raw,"checked":True}
    missing=[port for port,result in results.items() if not result["checked"]]
    extras=host.findall("ports/extraports")
    if len(extras)==1 and int(extras[0].get("count","0"))==len(missing):
        raw=extras[0].get("state","unknown")
        for port in missing:
            results[port]={"state":{"open":"O","closed":"C"}.get(raw,"U"),"udp_scan_state":raw,"checked":True}
    return results


def scan_udp(ip, ports, config, stop, lock):
    ip=str(IPv4Address(ip))
    ports=sorted(set(int(port) for port in ports))
    if not ports: return {}
    if any(not 1<=port<=65535 for port in ports): raise ValueError("Invalid UDP port")
    while not lock.acquire(timeout=0.2):
        if stop.is_set(): raise RuntimeError("UDP scan interrupted")
    try:
        if stop.is_set(): raise RuntimeError("UDP scan interrupted")
        options=config.get("udp",{})
        rate=config["discovery"]["packets_per_second"]/2
        limit=options.get("host_timeout_seconds",120)
        command=["nmap","-sU","-Pn","-n","-p",','.join(map(str,ports)),
                 "--max-rate",str(rate),"--max-parallelism",str(config["discovery"]["concurrency"]),
                 "--max-retries","1","--host-timeout",f"{limit}s","-oX","-",ip]
        if options.get("version_detection",False): command.insert(2,"-sV");command.insert(3,"--version-light")
        log.info("Nmap UDP started: ip=%s ports=%s",ip,','.join(map(str,ports)))
        with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) as process:
            deadline=time.monotonic()+limit+30
            try:
                while True:
                    if stop.is_set(): raise RuntimeError("UDP scan interrupted")
                    if time.monotonic()>deadline: raise RuntimeError("Nmap UDP process timeout")
                    try:
                        output,errors=process.communicate(timeout=0.2)
                        break
                    except subprocess.TimeoutExpired: continue
                if process.returncode!=0: raise RuntimeError("Nmap UDP failed; verify Nmap installation and NET_RAW capability")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:process.communicate(timeout=2)
                    except subprocess.TimeoutExpired:process.kill();process.communicate()
        results=parse_udp_xml(output,ip,ports)
        for port,result in results.items():
            log.info("Nmap UDP result: ip=%s port=%s state=%s nmap_state=%s",ip,port,result["state"],result["udp_scan_state"])
        return results
    finally:
        lock.release()
