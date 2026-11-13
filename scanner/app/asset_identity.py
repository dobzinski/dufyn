"""Optional NetBIOS names for discovered hosts; DNS identity is resolved by the controller."""
import concurrent.futures
import logging
import secrets
import socket
import struct
import time

import dns.exception
import dns.name

log = logging.getLogger('scanner')

def netbios_identity(ip, settings, stop):
    transaction = secrets.randbelow(65536)
    name = b'*' + b'\0' * 15
    encoded = bytes(value for byte in name for value in (65 + (byte >> 4), 65 + (byte & 15)))
    query = struct.pack('!6H', transaction, 0, 1, 0, 0, 0) + b'\x20' + encoded + b'\0\x00\x21\x00\x01'
    for _ in range(settings['attempts']):
        if stop.is_set():
            raise RuntimeError('Identity discovery interrupted')
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
                connection.settimeout(settings['timeout_seconds'])
                connection.connect((ip, settings['port']))
                connection.send(query)
                packet = connection.recv(4096)
            if len(packet) < 12:
                continue
            response_id, flags, questions, answers, _, _ = struct.unpack('!6H', packet[:12])
            if response_id != transaction or not flags & 0x8000 or flags & 0x020f:
                continue
            offset = 12
            for _ in range(questions):
                _, size = dns.name.from_wire(packet, offset)
                offset += size + 4
            names = set()
            macs = set()
            for _ in range(answers):
                _, size = dns.name.from_wire(packet, offset)
                offset += size
                record_type, record_class, _, length = struct.unpack('!HHIH', packet[offset:offset + 10])
                offset += 10
                data = packet[offset:offset + length]
                offset += length
                if record_type != 33 or record_class != 1 or not data or len(data) < 1 + data[0] * 18:
                    continue
                for index in range(data[0]):
                    entry = data[1 + index * 18:19 + index * 18]
                    name_flags = int.from_bytes(entry[16:18], 'big')
                    if entry[15] == 0 and name_flags & 0x0400 and not name_flags & 0x8000:
                        value = entry[:15].decode('ascii').strip().lower()
                        if value and all(character.isalnum() or character in '-_' for character in value):
                            names.add(value)
                statistics = 1 + data[0] * 18
                mac = data[statistics:statistics + 6]
                if len(mac) == 6 and any(mac) and not mac[0] & 1:
                    macs.add(':'.join(f'{byte:02x}' for byte in mac))
            if names or macs:
                return {'display_name': names.pop() if len(names) == 1 else None,
                        'mac_address': macs.pop() if len(macs) == 1 else None}
        except (OSError, ValueError, UnicodeError, struct.error, dns.exception.DNSException):
            continue
    return {'display_name': None, 'mac_address': None}


def identify_host(host, config, stop):
    result = dict(host)
    if config['netbios']['enabled']:
        result.update(netbios_identity(host['ip'], config['netbios'], stop))
    return result


def identify_hosts(hosts, config, stop):
    if not config['netbios']['enabled']:
        return hosts
    with concurrent.futures.ThreadPoolExecutor(max_workers=config['discovery']['concurrency']) as pool:
        return list(pool.map(lambda host: identify_host(host, config, stop), hosts))


def reconcile_cycle(client, config):
    body = {'worker_id': config['worker_id'], 'cycle_id': config['_identity_cycle']['cycle_id']}
    attempts = config['execution']['result_attempts']
    for attempt in range(attempts):
        try:
            result = client.post('discovery/reconcile', body)
            if result.get('state') != 'completed':
                raise RuntimeError('Controller did not acknowledge discovery reconciliation')
            log.info('Discovery reconciliation completed: tasks=%s',result['reconciled_tasks'])
            return result
        except Exception as error:
            if attempt + 1 == attempts:
                raise RuntimeError('Discovery reconciliation failed; observations retained in the controller') from error
            time.sleep(config['execution']['retry_interval_seconds'])
