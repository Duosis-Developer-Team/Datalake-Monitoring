-- ============================================================
-- Datalake Monitoring - VMware Host Veri Güncelliği Kontrol Tablosu
-- ============================================================
-- Netbox envanter tablosu (discovery_netbox_inventory_device) ile
-- VMware (discovery_vmware_inventory_host) ve Nutanix
-- (discovery_nutanix_inventory_host) veri toplama tabloları
-- karşılaştırılarak sonuçlar saklanır.
--
-- Filtre:
--   tags1_display IN ('ESXI KLASIK HOST','ESXI KLASIK SAP HOST',
--                     'ESXI NUTANIX','ESXI NUTANIX HOST',
--                     'HYBRID','HYPER-V','KLASIK MIMARI')
--   AND tenant_name = 'Bulutistan - Virtualization'
--
-- Eşleştirme:
--   VMware:  name bazlı  (Netbox.name = VMware.name)
--   Nutanix: serial bazlı (Netbox.serial = Nutanix.serial)
--
-- finding_type:
--   STALE   = Her iki tabloda da last_observed 1 haftadan eski
--   MISSING = Her iki tabloda da hiç kaydı yok
-- ============================================================

CREATE SCHEMA IF NOT EXISTS hmdl;

CREATE TABLE hmdl.hmdl_datalake_monitoring_vmware_host (
    id BIGSERIAL PRIMARY KEY,

    -- Kontrol Bilgileri
    check_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    check_threshold_hours INT NOT NULL DEFAULT 168,

    -- Netbox (Envanter) Bilgileri
    netbox_device_id BIGINT,
    netbox_device_name TEXT NOT NULL,
    netbox_site_name TEXT,
    netbox_location_name TEXT,
    netbox_rack_name TEXT,
    netbox_status_value TEXT,
    netbox_serial TEXT,
    netbox_device_type_name TEXT,
    netbox_manufacturer_name TEXT,
    netbox_device_role_name TEXT,
    netbox_tenant_name TEXT,
    netbox_primary_ip_address TEXT,
    netbox_tags1_display TEXT,
    netbox_total_cores TEXT,
    netbox_total_ram TEXT,
    netbox_cpu TEXT,

    -- VMware (Veri Toplama) Bilgileri
    vmware_last_observed TIMESTAMPTZ,
    vmware_status TEXT,
    vmware_status_description TEXT,
    vmware_component_moid VARCHAR(255),
    vmware_vcenter_uuid VARCHAR(255),
    vmware_name TEXT,
    vmware_model TEXT,
    vmware_version TEXT,

    -- Nutanix (Veri Toplama) Bilgileri
    nutanix_last_observed TIMESTAMPTZ,
    nutanix_status TEXT,
    nutanix_status_description TEXT,
    nutanix_component_moid VARCHAR(255),
    nutanix_uuid VARCHAR(255),
    nutanix_name TEXT,
    nutanix_serial VARCHAR(255),
    nutanix_model TEXT,

    -- Hesaplanan Alanlar
    most_recent_source VARCHAR(10),      -- 'VMWARE' veya 'NUTANIX'
    most_recent_observed TIMESTAMPTZ,    -- İki kaynaktan en güncel last_observed
    data_age_hours NUMERIC(10,2),
    finding_type VARCHAR(20) NOT NULL,   -- 'STALE' veya 'MISSING'

    -- Metadata
    notes TEXT
);

-- İndeksler
CREATE INDEX idx_monitoring_vmware_host_check_time ON hmdl.hmdl_datalake_monitoring_vmware_host(check_time);
CREATE INDEX idx_monitoring_vmware_host_name       ON hmdl.hmdl_datalake_monitoring_vmware_host(netbox_device_name);
CREATE INDEX idx_monitoring_vmware_host_finding    ON hmdl.hmdl_datalake_monitoring_vmware_host(finding_type);
CREATE INDEX idx_monitoring_vmware_host_serial     ON hmdl.hmdl_datalake_monitoring_vmware_host(netbox_serial);

COMMENT ON TABLE hmdl.hmdl_datalake_monitoring_vmware_host IS
    'Datalake VMware Host veri güncelliği monitoring sonuçları. VMware ve Nutanix tabloları birlikte kontrol edilir; en güncel kaynak baz alınır.';
COMMENT ON COLUMN hmdl.hmdl_datalake_monitoring_vmware_host.most_recent_source IS
    'En güncel verinin kaynağı: VMWARE veya NUTANIX';
COMMENT ON COLUMN hmdl.hmdl_datalake_monitoring_vmware_host.most_recent_observed IS
    'VMware ve Nutanix last_observed değerlerinden en güncel olanı';
