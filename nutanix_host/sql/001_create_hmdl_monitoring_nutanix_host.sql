-- ============================================================
-- Datalake Monitoring - Host Veri Güncelliği Kontrol Tablosu
-- ============================================================
-- Bu script hmdl şeması altında Nutanix Host monitoring
-- wide tablosunu oluşturur.
--
-- Netbox envanter tablosu (discovery_netbox_inventory_device) ile
-- Nutanix veri toplama tablosu (discovery_nutanix_inventory_host)
-- serial bazlı karşılaştırma sonuçlarını saklar.
--
-- finding_type:
--   STALE   = Nutanix'te kayıt var ama last_observed 1 haftadan eski
--   MISSING = Netbox'ta var ama Nutanix tablosunda hiç kaydı yok
-- ============================================================

-- Şema oluştur (yoksa)
CREATE SCHEMA IF NOT EXISTS hmdl;

-- Wide tablo oluştur
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

    -- Hesaplanan Alanlar
    data_age_hours NUMERIC(10,2),
    finding_type VARCHAR(20) NOT NULL,

    -- Metadata
    notes TEXT
);

-- İndeksler
CREATE INDEX idx_monitoring_host_check_time ON hmdl.hmdl_datalake_monitoring_nutanix_host(check_time);
CREATE INDEX idx_monitoring_host_name ON hmdl.hmdl_datalake_monitoring_nutanix_host(netbox_device_name);
CREATE INDEX idx_monitoring_host_finding ON hmdl.hmdl_datalake_monitoring_nutanix_host(finding_type);
CREATE INDEX idx_monitoring_host_serial ON hmdl.hmdl_datalake_monitoring_nutanix_host(netbox_serial);

-- Yorum ekle
COMMENT ON TABLE hmdl.hmdl_datalake_monitoring_nutanix_host IS 'Datalake Nutanix Host veri güncelliği monitoring sonuçları. Her scheduled çalışmada append edilir.';
COMMENT ON COLUMN hmdl.hmdl_datalake_monitoring_nutanix_host.finding_type IS 'STALE: Veri 1 haftadan eski, MISSING: Nutanix tablosunda hiç kayıt yok';
COMMENT ON COLUMN hmdl.hmdl_datalake_monitoring_nutanix_host.data_age_hours IS 'last_observed ile kontrol zamanı arasındaki fark (saat). MISSING durumunda NULL.';
