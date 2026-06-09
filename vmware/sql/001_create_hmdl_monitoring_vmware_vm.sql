-- ============================================================
-- Datalake Monitoring - VMware VM Veri Güncelliği Kontrol Tablosu
-- ============================================================
-- Netbox envanter tablosu (discovery_netbox_virtualization_vm) ile
-- VMware veri toplama tablosu (discovery_vmware_inventory_vm)
-- MOID bazlı karşılaştırma sonuçlarını saklar.
--
-- Eşleştirme:
--   Netbox.custom_fields_moid = VMware.component_moid
--
-- Filtre:
--   Netbox: tags1_display LIKE '%VMware%'
--          AND custom_fields_moid IS NOT NULL
--
-- finding_type:
--   STALE   = VMware'de kayıt var ama last_observed 1 haftadan eski
--   MISSING = Netbox'ta var ama VMware tablosunda hiç kaydı yok
-- ============================================================

CREATE SCHEMA IF NOT EXISTS hmdl;

CREATE TABLE hmdl.hmdl_datalake_monitoring_vmware_vm (
    id BIGSERIAL PRIMARY KEY,

    -- Kontrol Bilgileri
    check_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    check_threshold_hours INT NOT NULL DEFAULT 168,

    -- Netbox (Envanter) Bilgileri
    netbox_vm_id BIGINT,
    netbox_vm_name TEXT NOT NULL,
    netbox_site_name TEXT,
    netbox_cluster_name TEXT,
    netbox_status_value TEXT,
    netbox_custom_fields_moid TEXT,         -- Eşleştirme anahtarı (ör: vm-105713)
    netbox_custom_fields_uuid TEXT,
    netbox_vcpus NUMERIC,
    netbox_memory NUMERIC,
    netbox_disk NUMERIC,
    netbox_guest_os TEXT,
    netbox_musteri TEXT,
    netbox_endpoint TEXT,

    -- VMware (Veri Toplama) Bilgileri
    vmware_last_observed TIMESTAMPTZ,
    vmware_status TEXT,
    vmware_status_description TEXT,
    vmware_component_moid VARCHAR(255),
    vmware_vcenter_uuid VARCHAR(255),
    vmware_name TEXT,
    vmware_guest_os TEXT,
    vmware_tools_status TEXT,

    -- Hesaplanan Alanlar
    data_age_hours NUMERIC(10,2),
    finding_type VARCHAR(20) NOT NULL,

    -- Metadata
    notes TEXT
);

-- İndeksler
CREATE INDEX idx_monitoring_vmware_vm_check_time  ON hmdl.hmdl_datalake_monitoring_vmware_vm(check_time);
CREATE INDEX idx_monitoring_vmware_vm_name        ON hmdl.hmdl_datalake_monitoring_vmware_vm(netbox_vm_name);
CREATE INDEX idx_monitoring_vmware_vm_finding     ON hmdl.hmdl_datalake_monitoring_vmware_vm(finding_type);
CREATE INDEX idx_monitoring_vmware_vm_moid        ON hmdl.hmdl_datalake_monitoring_vmware_vm(netbox_custom_fields_moid);

COMMENT ON TABLE hmdl.hmdl_datalake_monitoring_vmware_vm IS
    'Datalake VMware VM veri güncelliği monitoring sonuçları. Netbox VMware tag''lı VM''ler ile discovery_vmware_inventory_vm MOID bazlı karşılaştırılır.';
COMMENT ON COLUMN hmdl.hmdl_datalake_monitoring_vmware_vm.finding_type IS
    'STALE: Veri 1 haftadan eski, MISSING: VMware tablosunda hiç kayıt yok';
