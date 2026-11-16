BEGIN;
ALTER TABLE network ADD COLUMN IF NOT EXISTS dhcp CHAR(1) NOT NULL DEFAULT 'N' CHECK (dhcp IN ('Y','N'));
ALTER TABLE asset ALTER COLUMN ip DROP NOT NULL;
ALTER TABLE asset ADD COLUMN IF NOT EXISTS last_known_ip INET;
UPDATE asset SET last_known_ip=ip WHERE last_known_ip IS NULL AND ip IS NOT NULL;
CREATE TABLE IF NOT EXISTS discovery_identity (
    network_id BIGINT NOT NULL REFERENCES network(id) ON DELETE CASCADE,
    ip INET NOT NULL,
    hostname VARCHAR(255),
    identity_status VARCHAR(8) NOT NULL CHECK (identity_status IN ('verified','wait','missing','conflict')),
    reason VARCHAR(255),
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (network_id,ip)
);
CREATE INDEX IF NOT EXISTS idx_asset_hostname ON asset(site_id,lower(rtrim(discovered_hostname,'.')))
    WHERE discovered_hostname IS NOT NULL;
ALTER TABLE discovery_task ADD COLUMN IF NOT EXISTS config_snapshot JSONB;
ALTER TABLE discovery_task ADD COLUMN IF NOT EXISTS reconciled_at TIMESTAMPTZ;
ALTER TABLE discovery_identity ADD COLUMN IF NOT EXISTS task_id UUID REFERENCES discovery_task(id) ON DELETE CASCADE;
ALTER TABLE asset ADD COLUMN IF NOT EXISTS mac_address MACADDR;
COMMIT;
