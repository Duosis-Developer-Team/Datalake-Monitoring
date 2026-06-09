-- ============================================================
-- Datalake Monitoring - Host Veri Güncelliği Kontrol Tablosu
-- ============================================================
-- Netbox envanter tablosu (discovery_netbox_inventory_device) ile
-- Nutanix (discovery_nutanix_inventory_host) ve VMware
-- (discovery_vmware_inventory_host) veri toplama tabloları
-- karşılaştırılarak sonuçlar saklanır.
--
-- Eşleştirme:
--   Nutanix: serial bazlı (Netbox.serial = Nutanix.serial)
--   VMware:  name bazlı   (Netbox.name = VMware.name)
--
-- finding_type:
--   STALE   = Her iki tabloda da last_observed 1 haftadan eski
--   MISSING = Her iki tabloda da hiç kaydı yok
-- ============================================================

CREATE SCHEMA IF NOT EXISTS hmdl;

-- Eski tablo varsa düşür (ilk kurulumda gerekli değil)
-- DROP TABLE IF EXISTS hmdl.hmdl_datalake_monitoring_nutanix_host;

CREATE TABLE hmdl.hmdl_datalake_monitoring_nutanix_host (
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

    -- Nutanix (Veri Toplama) Bilgileri
    nutanix_last_observed TIMESTAMPTZ,
    nutanix_status TEXT,
    nutanix_status_description TEXT,
    nutanix_component_moid VARCHAR(255),
    nutanix_uuid VARCHAR(255),
    nutanix_name TEXT,
    nutanix_serial VARCHAR(255),
    nutanix_model TEXT,

    -- VMware (Veri Toplama) Bilgileri
    vmware_last_observed TIMESTAMPTZ,
    vmware_status TEXT,
    vmware_status_description TEXT,
    vmware_component_moid VARCHAR(255),
    vmware_name TEXT,
    vmware_model TEXT,
    vmware_version TEXT,

    -- Hesaplanan Alanlar
    most_recent_source VARCHAR(10),     -- 'NUTANIX' veya 'VMWARE' — en güncel veri hangi kaynaktan
    most_recent_observed TIMESTAMPTZ,   -- İki kaynaktan en güncel last_observed
    data_age_hours NUMERIC(10,2),       -- most_recent_observed'dan bu yana geçen saat
    finding_type VARCHAR(20) NOT NULL,  -- 'STALE' veya 'MISSING'

    -- Metadata
    notes TEXT
);

-- İndeksler
CREATE INDEX idx_monitoring_host_check_time ON hmdl.hmdl_datalake_monitoring_nutanix_host(check_time);
CREATE INDEX idx_monitoring_host_name ON hmdl.hmdl_datalake_monitoring_nutanix_host(netbox_device_name);
CREATE INDEX idx_monitoring_host_finding ON hmdl.hmdl_datalake_monitoring_nutanix_host(finding_type);
CREATE INDEX idx_monitoring_host_serial ON hmdl.hmdl_datalake_monitoring_nutanix_host(netbox_serial);

COMMENT ON TABLE hmdl.hmdl_datalake_monitoring_nutanix_host IS 'Datalake Nutanix Host veri güncelliği monitoring sonuçları. Nutanix ve VMware tablolarının her ikisi de kontrol edilir.';
COMMENT ON COLUMN hmdl.hmdl_datalake_monitoring_nutanix_host.most_recent_source IS 'En güncel verinin kaynağı: NUTANIX veya VMWARE';
COMMENT ON COLUMN hmdl.hmdl_datalake_monitoring_nutanix_host.most_recent_observed IS 'Nutanix ve VMware last_observed değerlerinden en güncel olanı';
