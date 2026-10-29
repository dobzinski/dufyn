-- Network Discovery - PostgreSQL initial schema
-- All timestamps are TIMESTAMPTZ; application should send UTC.

CREATE TABLE site (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    description VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE port_catalog (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
    protocol CHAR(1) NOT NULL DEFAULT 'T' CHECK (protocol IN ('T', 'U')),
    service VARCHAR(100),
    UNIQUE (port, protocol)
);

-- Starter catalog: common services; administrators select site/network assignments.
-- Reference: https://www.iana.org/assignments/service-names-port-numbers/
INSERT INTO port_catalog (port, protocol, service) VALUES
    (20, 'T', 'FTP Data'),
    (21, 'T', 'FTP'),
    (22, 'T', 'SSH'),
    (23, 'T', 'Telnet'),
    (25, 'T', 'SMTP'),
    (53, 'T', 'DNS'),
    (53, 'U', 'DNS'),
    (67, 'U', 'DHCP Server'),
    (80, 'T', 'HTTP'),
    (110, 'T', 'POP3'),
    (123, 'U', 'NTP'),
    (135, 'T', 'Microsoft RPC'),
    (143, 'T', 'IMAP'),
    (161, 'U', 'SNMP'),
    (162, 'U', 'SNMP Trap'),
    (389, 'T', 'LDAP'),
    (443, 'T', 'HTTPS'),
    (443, 'U', 'HTTP/3'),
    (445, 'T', 'SMB'),
    (465, 'T', 'SMTP TLS'),
    (514, 'U', 'Syslog'),
    (636, 'T', 'LDAP TLS'),
    (853, 'T', 'DNS TLS'),
    (993, 'T', 'IMAP TLS'),
    (995, 'T', 'POP3 TLS'),
    (2049, 'T', 'NFS'),
    (3389, 'T', 'RDP'),
    (3389, 'U', 'RDP'),
    (8080, 'T', 'HTTP Alternate');

CREATE TABLE network (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    site_id BIGINT NOT NULL REFERENCES site(id) ON DELETE RESTRICT,
    cidr CIDR NOT NULL,
    name VARCHAR(100),
    enabled CHAR(1) NOT NULL DEFAULT 'Y' CHECK (enabled IN ('Y', 'N')),
    -- I: inherit site ports; C: use only explicitly assigned network ports
    ptr_enabled CHAR(1) NOT NULL DEFAULT 'N' CHECK (ptr_enabled IN ('Y', 'N')),
    port_mode CHAR(1) NOT NULL DEFAULT 'I' CHECK (port_mode IN ('I', 'C')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (site_id, cidr),
    UNIQUE (id, site_id)
);

CREATE TABLE scanner (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    site_id BIGINT NOT NULL REFERENCES site(id) ON DELETE RESTRICT,
    name VARCHAR(100) NOT NULL,
    enabled CHAR(1) NOT NULL DEFAULT 'Y' CHECK (enabled IN ('Y', 'N')),
    -- Store a hash of the token, never the plaintext token
    token_hash VARCHAR(64) CHECK (token_hash IS NULL OR token_hash ~ '^[0-9a-f]{64}$'),
    last_seen TIMESTAMPTZ,
    config_encrypted BYTEA,
    config_updated_at TIMESTAMPTZ,
    config_received_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (site_id, name),
    UNIQUE (id, site_id)
);

CREATE TABLE site_port_catalog (
    site_id BIGINT NOT NULL REFERENCES site(id) ON DELETE CASCADE,
    port_catalog_id BIGINT NOT NULL REFERENCES port_catalog(id) ON DELETE RESTRICT,
    PRIMARY KEY (site_id, port_catalog_id)
);

CREATE TABLE network_port_catalog (
    network_id BIGINT NOT NULL REFERENCES network(id) ON DELETE CASCADE,
    port_catalog_id BIGINT NOT NULL REFERENCES port_catalog(id) ON DELETE RESTRICT,
    PRIMARY KEY (network_id, port_catalog_id)
);

-- Composite FKs ensure a scanner can only be assigned networks in its own site.
CREATE TABLE scanner_network (
    scanner_id BIGINT NOT NULL,
    network_id BIGINT NOT NULL,
    site_id BIGINT NOT NULL,
    PRIMARY KEY (scanner_id, network_id),
    FOREIGN KEY (scanner_id, site_id) REFERENCES scanner(id, site_id) ON DELETE CASCADE,
    FOREIGN KEY (network_id, site_id) REFERENCES network(id, site_id) ON DELETE CASCADE
);

CREATE TABLE asset_type (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE CHECK (name = btrim(name) AND name <> '')
);

INSERT INTO asset_type (name) VALUES
    ('Firewall'), ('Gateway'), ('Router'), ('Switch'), ('Access Point'), ('Load Balancer'),
    ('Server'), ('Virtualization Host'), ('Virtual Machine'), ('Desktop'), ('Laptop'), ('Mobile'),
    ('Application Server'), ('Database Server'), ('DNS Server'), ('Directory Service'), ('Web Server'), ('Container Host'),
    ('Storage'), ('Printer'), ('Phone'), ('Camera'), ('UPS'), ('Air Conditioner'), ('Other'),
    ('Backup Server'), ('Monitoring Server'), ('Mail Server'), ('Print Server'), ('VPN Gateway'), ('Reverse Proxy'), ('Network Controller'), ('IoT Device'), ('PDU'), ('Internet Link');

CREATE TABLE operating_system (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE CHECK (name = btrim(name) AND name <> '')
);

INSERT INTO operating_system (name) VALUES
    ('Linux'), ('Windows'), ('OSx'), ('BSD'), ('Unix'), ('Android'), ('iOS'), ('Other');
    
COMMENT ON TABLE operating_system IS 'Operating system catalog for inventory assets.';

CREATE TABLE asset (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    site_id BIGINT NOT NULL REFERENCES site(id) ON DELETE RESTRICT,
    ip INET NOT NULL,
    asset_type_id BIGINT REFERENCES asset_type(id) ON DELETE RESTRICT,
    operating_system_id BIGINT REFERENCES operating_system(id) ON DELETE RESTRICT,
    discovered_hostname VARCHAR(255),
    display_name VARCHAR(255),
    discovered_by_icmp CHAR(1) NOT NULL DEFAULT 'N' CHECK (discovered_by_icmp IN ('Y', 'N')),
    latency_ms DOUBLE PRECISION CHECK (latency_ms IS NULL OR latency_ms >= 0),
    status CHAR(1) NOT NULL DEFAULT 'A' CHECK (status IN ('A', 'I', 'U')),
    first_seen TIMESTAMPTZ,
    last_seen TIMESTAMPTZ,
    last_seen_active TIMESTAMPTZ,
    UNIQUE (site_id, ip)
);

CREATE INDEX idx_asset_type ON asset(asset_type_id) WHERE asset_type_id IS NOT NULL;
CREATE INDEX idx_asset_operating_system ON asset(operating_system_id) WHERE operating_system_id IS NOT NULL;
COMMENT ON COLUMN asset.operating_system_id IS 'Manually assigned operating system; NULL means not identified.';
COMMENT ON COLUMN asset.asset_type_id IS 'Inventory classification shared by every topology view; NULL means unclassified.';

CREATE TABLE target (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    site_id BIGINT NOT NULL REFERENCES site(id) ON DELETE RESTRICT,
    fqdn VARCHAR(253) NOT NULL,
    status CHAR(1) NOT NULL DEFAULT 'U' CHECK (status IN ('A', 'I', 'U')),
    latency_ms DOUBLE PRECISION CHECK (latency_ms IS NULL OR latency_ms >= 0),
    last_seen TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (fqdn = lower(fqdn) AND fqdn = btrim(fqdn) AND fqdn <> ''),
    UNIQUE (site_id, fqdn)
);

CREATE TABLE target_port_catalog (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    target_id BIGINT NOT NULL REFERENCES target(id) ON DELETE CASCADE,
    port_catalog_id BIGINT NOT NULL REFERENCES port_catalog(id) ON DELETE RESTRICT,
    UNIQUE (target_id, port_catalog_id)
);

CREATE TABLE port (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_id BIGINT REFERENCES asset(id) ON DELETE CASCADE,
    target_id BIGINT REFERENCES target(id) ON DELETE CASCADE,
    port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
    protocol CHAR(1) NOT NULL DEFAULT 'T' CHECK (protocol IN ('T', 'U')),
    state CHAR(1) NOT NULL DEFAULT 'O' CHECK (state IN ('O', 'C')),
    service VARCHAR(100),
    udp_scan_state VARCHAR(32),
    check_protocol VARCHAR(5) CHECK (check_protocol IN ('http','https')),
    last_http_status INTEGER,
    response_time_ms DOUBLE PRECISION CHECK (response_time_ms IS NULL OR (response_time_ms >= 0 AND response_time_ms < 'Infinity'::double precision)),
    first_seen TIMESTAMPTZ,
    last_seen TIMESTAMPTZ,
    last_seen_open TIMESTAMPTZ,
    CONSTRAINT port_destination_check CHECK (num_nonnulls(asset_id, target_id) = 1),
    UNIQUE (asset_id, port, protocol),
    UNIQUE (target_id, port, protocol)
);

CREATE TABLE certificate (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    port_id BIGINT UNIQUE REFERENCES port(id) ON DELETE CASCADE,
    asset_id BIGINT REFERENCES asset(id) ON DELETE CASCADE,
    subject_cn TEXT,
    issuer_cn TEXT,
    note VARCHAR(100),
    serial_number TEXT,
    sans JSONB,
    valid_from TIMESTAMPTZ NOT NULL,
    valid_until TIMESTAMPTZ NOT NULL,
    fingerprint_sha256 CHAR(64) CHECK (fingerprint_sha256 ~ '^[0-9a-fA-F]{64}$'),
    tls_version VARCHAR(30),
    cipher VARCHAR(150),
    last_seen TIMESTAMPTZ,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT certificate_owner_check CHECK (num_nonnulls(asset_id, port_id) = 1),
    CHECK (valid_until >= valid_from)
);

CREATE INDEX idx_network_site ON network(site_id);
CREATE INDEX idx_scanner_site ON scanner(site_id);
CREATE INDEX idx_site_port_catalog_port ON site_port_catalog(port_catalog_id);
CREATE INDEX idx_network_port_catalog_port ON network_port_catalog(port_catalog_id);
CREATE INDEX idx_scanner_network_network ON scanner_network(network_id);
CREATE INDEX idx_asset_status ON asset(site_id, status);
CREATE INDEX idx_target_site ON target(site_id);
CREATE INDEX idx_port_state ON port(state);
CREATE INDEX idx_certificate_asset ON certificate(asset_id);
CREATE INDEX idx_certificate_valid_until ON certificate(valid_until);

COMMENT ON COLUMN network.port_mode IS 'I=inherit site port catalog; C=custom network port catalog';
COMMENT ON COLUMN asset.status IS 'A=active; I=inactive; U=unknown';
COMMENT ON COLUMN target.status IS 'A=active ICMP; I=inactive ICMP; U=skip ICMP gating';
COMMENT ON COLUMN port.state IS 'O=open; C=closed';
COMMENT ON COLUMN port.protocol IS 'T=TCP; U=UDP';
COMMENT ON COLUMN scanner.token_hash IS 'Store a secure hash of the scanner authentication token';


CREATE UNIQUE INDEX idx_scanner_token_hash ON scanner(token_hash) WHERE token_hash IS NOT NULL;
CREATE TABLE discovery_task (
    id UUID PRIMARY KEY,
    scanner_id BIGINT NOT NULL,
    network_id BIGINT NOT NULL,
    site_id BIGINT NOT NULL,
    worker_id VARCHAR(100) NOT NULL,
    scope VARCHAR(9) NOT NULL DEFAULT 'discovery' CHECK (scope IN ('discovery','ptr')),
    state VARCHAR(12) NOT NULL DEFAULT 'running' CHECK (state IN ('running', 'completed', 'expired')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_until TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    result_hash CHAR(64),
    FOREIGN KEY (scanner_id, site_id) REFERENCES scanner(id, site_id) ON DELETE RESTRICT,
    FOREIGN KEY (network_id, site_id) REFERENCES network(id, site_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX idx_discovery_running ON discovery_task(network_id) WHERE state = 'running';
CREATE INDEX idx_discovery_history ON discovery_task(network_id, completed_at);

CREATE TABLE target_task (
    config_snapshot JSONB,
    id UUID PRIMARY KEY,
    scanner_id BIGINT NOT NULL,
    target_id BIGINT NOT NULL REFERENCES target(id) ON DELETE CASCADE,
    site_id BIGINT NOT NULL,
    fqdn VARCHAR(253) NOT NULL,
    worker_id VARCHAR(100) NOT NULL,
    state VARCHAR(12) NOT NULL DEFAULT 'running' CHECK (state IN ('running', 'completed', 'expired')),
    lease_until TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    result_hash CHAR(64),
    FOREIGN KEY (scanner_id, site_id) REFERENCES scanner(id, site_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX idx_target_task_running ON target_task(target_id) WHERE state = 'running';

CREATE TABLE asset_port_task (
    id UUID PRIMARY KEY,
    scanner_id BIGINT NOT NULL,
    asset_id BIGINT NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    site_id BIGINT NOT NULL,
    worker_id VARCHAR(100) NOT NULL,
    config_snapshot JSONB NOT NULL,
    state VARCHAR(12) NOT NULL DEFAULT 'running' CHECK (state IN ('running','completed','expired')),
    lease_until TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    result_hash CHAR(64),
    FOREIGN KEY (scanner_id, site_id) REFERENCES scanner(id, site_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX idx_asset_port_task_running ON asset_port_task(asset_id) WHERE state = 'running';

-- DNS Validation schema (migration 016 for existing databases).
-- DNS inventory is independent of scanner_network: a DNS-only scanner needs no networks.
ALTER TABLE asset ADD CONSTRAINT asset_id_site_unique UNIQUE (id, site_id);

CREATE TABLE dns_zone (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    site_id BIGINT NOT NULL REFERENCES site(id) ON DELETE RESTRICT,
    name VARCHAR(253) NOT NULL,
    dns_server INET NOT NULL,
    dns_port INTEGER NOT NULL DEFAULT 53 CHECK (dns_port BETWEEN 1 AND 65535),
    last_scanner_id BIGINT,
    serial BIGINT CHECK (serial BETWEEN 0 AND 4294967295),
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (name = lower(name) AND name = btrim(name) AND name <> '' AND right(name, 1) <> '.'),
    CHECK (masklen(dns_server) = CASE family(dns_server) WHEN 4 THEN 32 ELSE 128 END),
    FOREIGN KEY (last_scanner_id, site_id) REFERENCES scanner(id, site_id)
        ON DELETE SET NULL (last_scanner_id),
    UNIQUE (site_id, dns_server, dns_port, name)
);

CREATE TABLE dns_record (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    zone_id BIGINT NOT NULL REFERENCES dns_zone(id) ON DELETE CASCADE,
    name VARCHAR(253) NOT NULL,
    record_type VARCHAR(5) NOT NULL CHECK (record_type IN ('A', 'AAAA', 'CNAME')),
    address INET,
    target_name VARCHAR(253),
    ttl BIGINT NOT NULL CHECK (ttl BETWEEN 0 AND 4294967295),
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (name = lower(name) AND name = btrim(name) AND name <> '' AND right(name, 1) <> '.'),
    CHECK (target_name IS NULL OR
        (target_name = lower(target_name) AND target_name = btrim(target_name)
         AND target_name <> '' AND right(target_name, 1) <> '.')),
    CONSTRAINT dns_record_value_check CHECK (
        (record_type = 'A' AND address IS NOT NULL AND family(address) = 4 AND target_name IS NULL) OR
        (record_type = 'AAAA' AND address IS NOT NULL AND family(address) = 6 AND target_name IS NULL) OR
        (record_type = 'CNAME' AND address IS NULL AND target_name IS NOT NULL)
    ),
    CHECK (address IS NULL OR masklen(address) = CASE family(address) WHEN 4 THEN 32 ELSE 128 END),
    CHECK (last_seen >= first_seen),
    UNIQUE NULLS NOT DISTINCT (zone_id, name, record_type, address, target_name)
);
CREATE UNIQUE INDEX idx_dns_record_cname ON dns_record(zone_id, name) WHERE record_type = 'CNAME';
CREATE INDEX idx_dns_record_address ON dns_record(address) WHERE address IS NOT NULL;
CREATE INDEX idx_dns_record_name ON dns_record(name);
CREATE INDEX idx_dns_record_target ON dns_record(target_name) WHERE target_name IS NOT NULL;
CREATE INDEX idx_dns_zone_scanner ON dns_zone(last_scanner_id) WHERE last_scanner_id IS NOT NULL;

CREATE TABLE dns_reverse_check (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    site_id BIGINT NOT NULL REFERENCES site(id) ON DELETE RESTRICT,
    scanner_id BIGINT,
    asset_id BIGINT,
    ip INET NOT NULL,
    dns_server INET NOT NULL,
    dns_port INTEGER NOT NULL DEFAULT 53 CHECK (dns_port BETWEEN 1 AND 65535),
    result VARCHAR(12) NOT NULL CHECK (result IN
        ('answered', 'no_ptr', 'nxdomain', 'timeout', 'servfail', 'refused', 'error')),
    response_code INTEGER CHECK (response_code BETWEEN 0 AND 4095),
    error_message TEXT,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    icmp_responded BOOLEAN,
    CHECK (masklen(ip) = CASE family(ip) WHEN 4 THEN 32 ELSE 128 END),
    CHECK (masklen(dns_server) = CASE family(dns_server) WHEN 4 THEN 32 ELSE 128 END),
    FOREIGN KEY (scanner_id, site_id) REFERENCES scanner(id, site_id)
        ON DELETE SET NULL (scanner_id),
    FOREIGN KEY (asset_id, site_id) REFERENCES asset(id, site_id)
        ON DELETE SET NULL (asset_id)
);
CREATE INDEX idx_dns_reverse_site_ip ON dns_reverse_check(site_id, ip, checked_at DESC, id DESC);
CREATE INDEX idx_dns_reverse_asset ON dns_reverse_check(asset_id, checked_at DESC) WHERE asset_id IS NOT NULL;
CREATE INDEX idx_dns_reverse_scanner ON dns_reverse_check(scanner_id) WHERE scanner_id IS NOT NULL;

CREATE TABLE dns_ptr_record (
    site_id BIGINT NOT NULL REFERENCES site(id) ON DELETE RESTRICT,
    ip INET NOT NULL,
    first_seen TIMESTAMPTZ NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL,
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reverse_check_id BIGINT NOT NULL REFERENCES dns_reverse_check(id) ON DELETE CASCADE,
    hostname VARCHAR(253) NOT NULL,
    ttl BIGINT NOT NULL CHECK (ttl BETWEEN 0 AND 4294967295),
    CHECK (hostname = lower(hostname) AND hostname = btrim(hostname)
        AND hostname <> '' AND right(hostname, 1) <> '.'),
    UNIQUE (reverse_check_id, hostname),
    CONSTRAINT dns_ptr_site_ip_hostname_key UNIQUE (site_id,ip,hostname)
);
CREATE INDEX idx_dns_ptr_hostname ON dns_ptr_record(hostname);
CREATE FUNCTION set_dns_ptr_identity() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE observation dns_reverse_check%ROWTYPE;
BEGIN
    SELECT * INTO STRICT observation FROM dns_reverse_check WHERE id=NEW.reverse_check_id;
    NEW.site_id := observation.site_id;
    NEW.ip := observation.ip;
    NEW.first_seen := COALESCE(NEW.first_seen,observation.checked_at);
    NEW.last_seen := COALESCE(NEW.last_seen,observation.checked_at);
    RETURN NEW;
END;
$$;
CREATE TRIGGER dns_ptr_identity BEFORE INSERT OR UPDATE ON dns_ptr_record
    FOR EACH ROW EXECUTE FUNCTION set_dns_ptr_identity();


COMMENT ON TABLE dns_zone IS 'Zone identity is site + DNS server/port + name, independent of the collecting scanner.';
COMMENT ON COLUMN dns_zone.last_scanner_id IS 'Provenance of the last successful zone import, not exclusive ownership.';
COMMENT ON COLUMN dns_zone.serial IS 'Unsigned 32-bit SOA serial; serial arithmetic is handled by the collector.';
COMMENT ON COLUMN dns_zone.last_synced_at IS 'Advance only after a complete successful import commits atomically.';
COMMENT ON TABLE dns_record IS 'Current successful zone snapshot; only A, AAAA and CNAME are imported initially.';
COMMENT ON COLUMN dns_record.name IS 'Absolute DNS owner name, lowercase ASCII/IDNA, without trailing dot; expand @ before insertion.';
COMMENT ON TABLE dns_reverse_check IS 'Historical PTR queries from a scanner; collection errors are not DNS compliance failures.';
COMMENT ON COLUMN dns_reverse_check.ip IS 'IP at query time, retained even if its optional Asset is removed.';
COMMENT ON TABLE dns_ptr_record IS 'Current PTR answers unique by Site/IP/hostname; latest query provenance and stable first observation.';

-- Topology schema (migration 017 for existing databases).
CREATE TABLE topology_view (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    site_id BIGINT NOT NULL REFERENCES site(id) ON DELETE RESTRICT,
    name VARCHAR(100) NOT NULL CHECK (name = btrim(name) AND name <> ''),
    type VARCHAR(8) NOT NULL CHECK (type IN ('physical', 'logical')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (site_id, name, type),
    UNIQUE (id, site_id)
);

CREATE TABLE topology_asset (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    topology_view_id BIGINT NOT NULL,
    asset_id BIGINT NOT NULL,
    parent_id BIGINT,
    -- Derived from the view by the trigger; callers need not supply this field.
    site_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (topology_view_id, asset_id),
    UNIQUE (id, topology_view_id),
    CHECK (parent_id IS NULL OR parent_id <> id),
    FOREIGN KEY (topology_view_id, site_id) REFERENCES topology_view(id, site_id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id, site_id) REFERENCES asset(id, site_id) ON DELETE CASCADE,
    FOREIGN KEY (parent_id, topology_view_id) REFERENCES topology_asset(id, topology_view_id)
        ON DELETE SET NULL (parent_id)
);
CREATE INDEX idx_topology_asset_parent ON topology_asset(parent_id, topology_view_id) WHERE parent_id IS NOT NULL;
CREATE INDEX idx_topology_asset_asset ON topology_asset(asset_id, site_id);

CREATE FUNCTION validate_topology_asset() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    view_site_id BIGINT;
BEGIN
    -- Serialize hierarchy edits. A row version change also prevents stale ancestor
    -- reads under REPEATABLE READ: a competing edit must retry its transaction.
    UPDATE topology_view SET name = name WHERE id = NEW.topology_view_id
        RETURNING site_id INTO view_site_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Topology view does not exist' USING ERRCODE = '23503';
    END IF;
    NEW.site_id := view_site_id;
    IF NEW.parent_id IS NOT NULL THEN
        IF NEW.parent_id = NEW.id OR EXISTS (
            WITH RECURSIVE ancestor AS (
                SELECT id, parent_id FROM topology_asset
                WHERE id = NEW.parent_id AND topology_view_id = NEW.topology_view_id
                UNION
                SELECT item.id, item.parent_id FROM topology_asset item
                JOIN ancestor ON item.id = ancestor.parent_id
                WHERE item.topology_view_id = NEW.topology_view_id
            )
            SELECT 1 FROM ancestor WHERE id = NEW.id
        ) THEN
            RAISE EXCEPTION 'Topology hierarchy cannot contain cycles' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER topology_asset_validate BEFORE INSERT OR UPDATE ON topology_asset
    FOR EACH ROW EXECUTE FUNCTION validate_topology_asset();

COMMENT ON TABLE topology_view IS 'Named physical or logical hierarchy scoped to a Site; positions are reserved for a later stage.';
COMMENT ON TABLE asset_type IS 'Inventory asset classifications shared across topology views.';
COMMENT ON COLUMN topology_asset.parent_id IS 'Parent topology_asset in the same view; NULL represents an independent root.';
COMMENT ON COLUMN topology_asset.site_id IS 'Automatically derived from the view; composite foreign keys enforce asset/view Site consistency.';
COMMENT ON TABLE topology_asset IS 'Manual view membership. Deleting membership detaches children without deleting inventory assets.';

-- Logical interconnections (migration 032 for existing databases).
-- Logical links between mappings in different Sites and views.
CREATE TABLE topology_interconnection (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE CHECK (name = btrim(name) AND name <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE topology_interconnection IS 'Named multi-Site logical diagrams; participating views are derived from their links.';

CREATE TABLE topology_logical_link (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    interconnection_id BIGINT NOT NULL REFERENCES topology_interconnection(id) ON DELETE CASCADE,
    source_id BIGINT NOT NULL REFERENCES topology_asset(id) ON DELETE CASCADE,
    destination_id BIGINT NOT NULL REFERENCES topology_asset(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (source_id <> destination_id)
);

CREATE UNIQUE INDEX topology_logical_link_pair_unique ON topology_logical_link (
    interconnection_id, LEAST(source_id, destination_id), GREATEST(source_id, destination_id)
);
CREATE INDEX idx_topology_logical_link_interconnection ON topology_logical_link(interconnection_id);
CREATE INDEX idx_topology_logical_link_source ON topology_logical_link(source_id);
CREATE INDEX idx_topology_logical_link_destination ON topology_logical_link(destination_id);

CREATE FUNCTION validate_topology_logical_link() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    source_view BIGINT;
    destination_view BIGINT;
    source_site BIGINT;
    destination_site BIGINT;
    logical_count INTEGER;
BEGIN
    -- Keep endpoint membership and view types stable until this transaction ends.
    PERFORM m.id FROM topology_asset m JOIN topology_view v ON v.id=m.topology_view_id
        WHERE m.id IN (NEW.source_id, NEW.destination_id)
        ORDER BY v.id,m.id FOR SHARE OF m,v;
    SELECT a.topology_view_id,b.topology_view_id,va.site_id,vb.site_id,
        (CASE WHEN va.type='logical' THEN 1 ELSE 0 END + CASE WHEN vb.type='logical' THEN 1 ELSE 0 END)
        INTO source_view,destination_view,source_site,destination_site,logical_count
        FROM topology_asset a JOIN topology_view va ON va.id=a.topology_view_id
        CROSS JOIN topology_asset b JOIN topology_view vb ON vb.id=b.topology_view_id
        WHERE a.id=NEW.source_id AND b.id=NEW.destination_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Interconnection mapping not found' USING ERRCODE='23503';
    END IF;
    IF logical_count <> 2 OR source_view=destination_view OR source_site=destination_site THEN
        RAISE EXCEPTION 'Interconnections require logical views in different Sites' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER topology_logical_link_validate BEFORE INSERT OR UPDATE ON topology_logical_link
    FOR EACH ROW EXECUTE FUNCTION validate_topology_logical_link();

CREATE FUNCTION protect_interconnected_mapping() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.topology_view_id IS DISTINCT FROM OLD.topology_view_id AND EXISTS (
        SELECT 1 FROM topology_logical_link WHERE source_id=OLD.id OR destination_id=OLD.id
    ) THEN
        RAISE EXCEPTION 'Remove interconnections before moving this mapping to another view' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER topology_asset_interconnection_guard BEFORE UPDATE OF topology_view_id ON topology_asset
    FOR EACH ROW EXECUTE FUNCTION protect_interconnected_mapping();

CREATE FUNCTION protect_interconnected_view() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.type IS DISTINCT FROM OLD.type OR NEW.site_id IS DISTINCT FROM OLD.site_id) AND EXISTS (
        SELECT 1 FROM topology_asset m JOIN topology_logical_link l ON l.source_id=m.id OR l.destination_id=m.id
        WHERE m.topology_view_id=OLD.id
    ) THEN
        RAISE EXCEPTION 'Remove interconnections before changing this view type or Site' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER topology_view_interconnection_guard BEFORE UPDATE OF type,site_id ON topology_view
    FOR EACH ROW EXECUTE FUNCTION protect_interconnected_view();

COMMENT ON TABLE topology_logical_link IS 'Undirected links between logical mappings in different Sites; internal parent hierarchies remain independent.';
COMMENT ON COLUMN topology_logical_link.source_id IS 'Endpoint topology_asset.id, not an inventory asset ID.';
COMMENT ON COLUMN topology_logical_link.destination_id IS 'Endpoint topology_asset.id, not an inventory asset ID.';

-- Physical topology schema (migration 022 for existing databases).
CREATE TABLE topology_floor_plan (
    topology_view_id BIGINT PRIMARY KEY REFERENCES topology_view(id) ON DELETE CASCADE,
    image BYTEA NOT NULL CHECK (octet_length(image) BETWEEN 1 AND 10485760),
    media_type VARCHAR(10) NOT NULL CHECK (media_type IN ('image/png','image/jpeg')),
    width INTEGER NOT NULL CHECK (width BETWEEN 1 AND 16000),
    height INTEGER NOT NULL CHECK (height BETWEEN 1 AND 16000),
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE topology_position (
    topology_asset_id BIGINT PRIMARY KEY,
    topology_view_id BIGINT NOT NULL REFERENCES topology_floor_plan(topology_view_id) ON DELETE CASCADE,
    x DOUBLE PRECISION NOT NULL CHECK (x BETWEEN 0 AND 1),
    y DOUBLE PRECISION NOT NULL CHECK (y BETWEEN 0 AND 1),
    FOREIGN KEY (topology_asset_id,topology_view_id)
        REFERENCES topology_asset(id,topology_view_id) ON DELETE CASCADE
);
CREATE INDEX idx_topology_position_view ON topology_position(topology_view_id);

CREATE FUNCTION validate_topology_floor_plan() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM topology_view WHERE id=NEW.topology_view_id AND type='physical' FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Floor plans require a physical topology view' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER topology_floor_plan_validate BEFORE INSERT OR UPDATE ON topology_floor_plan
    FOR EACH ROW EXECUTE FUNCTION validate_topology_floor_plan();

CREATE FUNCTION protect_physical_topology_view() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.type <> 'physical' AND EXISTS (
        SELECT 1 FROM topology_floor_plan WHERE topology_view_id=OLD.id
    ) THEN
        RAISE EXCEPTION 'Remove the floor plan before changing this view type' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER topology_view_physical_type BEFORE UPDATE OF type ON topology_view
    FOR EACH ROW EXECUTE FUNCTION protect_physical_topology_view();

COMMENT ON TABLE topology_floor_plan IS 'Validated raster floor plan stored with the database; revision protects concurrent image/position edits.';
COMMENT ON TABLE topology_position IS 'Manual physical marker centers normalized to [0,1] in the floor plan, independent of viewport size.';

-- Private CA hierarchy and issued HTTPS certificates. Apply with psql -v ON_ERROR_STOP=1.
CREATE TABLE managed_certificate (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    parent_id BIGINT REFERENCES managed_certificate(id) ON DELETE RESTRICT,
    kind VARCHAR(12) NOT NULL CHECK (kind IN ('root', 'intermediate', 'server')),
    source VARCHAR(9) CONSTRAINT managed_certificate_source_check CHECK (source IN ('generated', 'imported')),
    name VARCHAR(100) NOT NULL UNIQUE CHECK (name = btrim(name) AND name <> ''),
    subject JSONB NOT NULL CHECK (jsonb_typeof(subject) = 'object'),
    alt_names JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(alt_names) = 'array'),
    validity_days INTEGER NOT NULL CHECK (validity_days BETWEEN 1 AND 36500),
    not_before TIMESTAMPTZ NOT NULL,
    not_after TIMESTAMPTZ NOT NULL CHECK (not_after > not_before),
    key_algorithm VARCHAR(10) NOT NULL CHECK (key_algorithm = 'RSA'),
    rsa_key_size INTEGER NOT NULL CHECK (rsa_key_size IN (2048, 3072, 4096)),
    signature_hash VARCHAR(10) NOT NULL CHECK (signature_hash IN ('SHA256', 'SHA384', 'SHA512')),
    serial VARCHAR(48) NOT NULL CHECK (serial ~ '^[1-9][0-9]*$'),
    fingerprint_sha256 VARCHAR(64) NOT NULL UNIQUE,
    certificate_pem BYTEA NOT NULL CHECK (octet_length(certificate_pem) > 0),
    private_key_pem BYTEA NOT NULL CHECK (octet_length(private_key_pem) > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((kind = 'root' AND parent_id IS NULL) OR (kind <> 'root' AND parent_id IS NOT NULL)),
    CHECK (parent_id IS NULL OR parent_id <> id)
);
CREATE INDEX idx_managed_certificate_parent ON managed_certificate(parent_id);
CREATE UNIQUE INDEX idx_managed_certificate_issuer_serial ON managed_certificate(parent_id,serial)
    WHERE parent_id IS NOT NULL;

CREATE FUNCTION validate_managed_certificate() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE issuer managed_certificate%ROWTYPE;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'Issued certificates are immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.parent_id IS NOT NULL THEN
        SELECT * INTO issuer FROM managed_certificate WHERE id=NEW.parent_id FOR SHARE;
        IF NOT FOUND OR issuer.kind NOT IN ('root', 'intermediate') THEN
            RAISE EXCEPTION 'Parent must be a certificate authority' USING ERRCODE='23514';
        END IF;
        IF NEW.not_before < issuer.not_before OR NEW.not_after > issuer.not_after THEN
            RAISE EXCEPTION 'Certificate validity must fit within issuer validity' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER managed_certificate_validate BEFORE INSERT OR UPDATE ON managed_certificate
    FOR EACH ROW EXECUTE FUNCTION validate_managed_certificate();
COMMENT ON TABLE managed_certificate IS 'Immutable private CAs and HTTPS certificates; private_key_pem contains encrypted PKCS8 PEM, never plaintext.';
COMMENT ON COLUMN managed_certificate.subject IS 'X.509 subject JSON object. Country, state, locality, organization and organizational_unit are optional; missing attributes are omitted. Import preserves the original certificate subject.';

-- Diagrams (migrations 034-036 for existing databases).
-- Named diagrams can contain inventory from any Site.
CREATE TABLE topology_diagram (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE CHECK (name = btrim(name) AND name <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE topology_diagram_member (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    diagram_id BIGINT NOT NULL REFERENCES topology_diagram(id) ON DELETE CASCADE,
    asset_id BIGINT REFERENCES asset(id) ON DELETE CASCADE,
    target_id BIGINT REFERENCES target(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (num_nonnulls(asset_id,target_id)=1),
    UNIQUE (diagram_id,asset_id),
    UNIQUE (diagram_id,target_id)
);
CREATE INDEX idx_topology_diagram_member_asset ON topology_diagram_member(asset_id) WHERE asset_id IS NOT NULL;
CREATE INDEX idx_topology_diagram_member_target ON topology_diagram_member(target_id) WHERE target_id IS NOT NULL;

CREATE TABLE topology_diagram_relation (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    diagram_id BIGINT NOT NULL REFERENCES topology_diagram(id) ON DELETE CASCADE,
    source_asset_id BIGINT REFERENCES asset(id) ON DELETE CASCADE,
    source_target_id BIGINT REFERENCES target(id) ON DELETE CASCADE,
    destination_asset_id BIGINT REFERENCES asset(id) ON DELETE CASCADE,
    destination_target_id BIGINT REFERENCES target(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (num_nonnulls(source_asset_id,source_target_id)=1),
    CHECK (num_nonnulls(destination_asset_id,destination_target_id)=1),
    CHECK (source_asset_id IS NULL OR destination_asset_id IS NULL OR source_asset_id<>destination_asset_id),
    CHECK (source_target_id IS NULL OR destination_target_id IS NULL OR source_target_id<>destination_target_id),
    FOREIGN KEY (diagram_id,source_asset_id) REFERENCES topology_diagram_member(diagram_id,asset_id) ON DELETE CASCADE,
    FOREIGN KEY (diagram_id,source_target_id) REFERENCES topology_diagram_member(diagram_id,target_id) ON DELETE CASCADE,
    FOREIGN KEY (diagram_id,destination_asset_id) REFERENCES topology_diagram_member(diagram_id,asset_id) ON DELETE CASCADE,
    FOREIGN KEY (diagram_id,destination_target_id) REFERENCES topology_diagram_member(diagram_id,target_id) ON DELETE CASCADE,
    UNIQUE (diagram_id,source_asset_id,destination_asset_id),
    UNIQUE (diagram_id,source_asset_id,destination_target_id),
    UNIQUE (diagram_id,source_target_id,destination_asset_id),
    UNIQUE (diagram_id,source_target_id,destination_target_id)
);
CREATE UNIQUE INDEX topology_diagram_relation_pair_unique ON topology_diagram_relation (
    diagram_id,
    LEAST((CASE WHEN source_asset_id IS NOT NULL THEN 'asset:'||source_asset_id ELSE 'target:'||source_target_id END),(CASE WHEN destination_asset_id IS NOT NULL THEN 'asset:'||destination_asset_id ELSE 'target:'||destination_target_id END)),
    GREATEST((CASE WHEN source_asset_id IS NOT NULL THEN 'asset:'||source_asset_id ELSE 'target:'||source_target_id END),(CASE WHEN destination_asset_id IS NOT NULL THEN 'asset:'||destination_asset_id ELSE 'target:'||destination_target_id END))
);
CREATE INDEX idx_topology_diagram_relation_source_asset ON topology_diagram_relation(source_asset_id) WHERE source_asset_id IS NOT NULL;
CREATE INDEX idx_topology_diagram_relation_source_target ON topology_diagram_relation(source_target_id) WHERE source_target_id IS NOT NULL;
CREATE INDEX idx_topology_diagram_relation_destination_asset ON topology_diagram_relation(destination_asset_id) WHERE destination_asset_id IS NOT NULL;
CREATE INDEX idx_topology_diagram_relation_destination_target ON topology_diagram_relation(destination_target_id) WHERE destination_target_id IS NOT NULL;

COMMENT ON TABLE topology_diagram IS 'Named diagrams spanning one or multiple Sites.';
COMMENT ON TABLE topology_diagram_member IS 'Exactly one Asset or Target per member; members without relations remain visible.';
COMMENT ON TABLE topology_diagram_relation IS 'Undirected associations. Both endpoints must be members of this diagram; reverse duplicates are not allowed.';

-- Rolling first-discovery monitoring (migration 039 for existing databases).
CREATE INDEX idx_asset_first_seen ON asset(first_seen) WHERE first_seen IS NOT NULL;
CREATE INDEX idx_target_created_at ON target(created_at);
CREATE INDEX idx_certificate_first_seen ON certificate(first_seen) WHERE first_seen IS NOT NULL;
CREATE INDEX idx_dns_record_first_seen ON dns_record(first_seen);
COMMENT ON COLUMN certificate.first_seen IS 'First observed current certificate identity; preserved on rescans and reset on fingerprint replacement. Legacy unknown dates remain NULL.';

-- Network PTR observations (migration 040 for existing databases).
COMMENT ON COLUMN dns_reverse_check.icmp_responded IS 'ICMP observation from the same network discovery run; NULL for legacy or standalone PTR checks.';

-- Optional Manager password storage (migration 044 for existing databases).
CREATE TABLE password_record (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE CHECK (name = btrim(name) AND name <> ''),
    account VARCHAR(255),
    url VARCHAR(255),
    note VARCHAR(100),
    password_encrypted BYTEA NOT NULL CHECK (octet_length(password_encrypted) > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    character_count SMALLINT NOT NULL DEFAULT 24 CHECK (character_count BETWEEN 1 AND 999),
    use_uppercase CHAR(1) NOT NULL DEFAULT 'Y' CHECK (use_uppercase IN ('Y', 'N')),
    use_lowercase CHAR(1) NOT NULL DEFAULT 'Y' CHECK (use_lowercase IN ('Y', 'N')),
    use_numbers CHAR(1) NOT NULL DEFAULT 'Y' CHECK (use_numbers IN ('Y', 'N')),
    use_symbols CHAR(1) NOT NULL DEFAULT 'Y' CHECK (use_symbols IN ('Y', 'N'))
);

COMMENT ON TABLE password_record IS 'Optional shared Manager password records; enabled by password.manager.';
COMMENT ON COLUMN password_record.password_encrypted IS 'Fernet authenticated ciphertext; requires persistent PKI_MASTER_KEY. Never store plaintext.';
