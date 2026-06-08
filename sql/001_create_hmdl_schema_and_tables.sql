-- ============================================================
-- Datalake Monitoring - VM Veri Güncelliği Kontrol Tablosu
-- ============================================================
-- Bu script hmdl şemasını ve VM monitoring wide tablosunu oluşturur.
-- Netbox envanterindeki VM'lerin Nutanix veri toplama tablosundaki
-- güncelliğini takip etmek için kullanılır.
--
-- finding_type:
--   STALE   = Nutanix'te kayıt var ama last_observed 1 haftadan eski
--   MISSING = Netbox'ta var ama Nutanix tablosunda hiç kaydı yok
-- ============================================================

-- Şema oluştur (yoksa)
CREATE SCHEMA IF NOT EXISTS hmdl;

-- Wide tablo oluştur
CREATE TABLE hmdl.hmdl_datalake_monitoring_vm (
    id BIGSERIAL PRIMARY KEY,

    -- Kontrol Bilgileri
    check_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- Kontrolün yapıldığı zaman
    check_threshold_hours INT NOT NULL DEFAULT 168,              -- Kullanılan eşik (saat cinsinden, 168 = 1 hafta)

    -- Netbox (Envanter) Bilgileri
    netbox_vm_id BIGINT,                          -- Netbox VM ID
    netbox_vm_name TEXT NOT NULL,                  -- VM adı (eşleştirme anahtarı)
    netbox_site_name TEXT,                         -- Site bilgisi
    netbox_cluster_name TEXT,                      -- Cluster bilgisi
    netbox_status_value TEXT,                      -- Netbox'taki status (poweredOn/poweredOff vb.)
    netbox_custom_fields_uuid TEXT,                -- UUID (Netbox custom field)
    netbox_custom_fields_moid TEXT,                -- MOID (Netbox custom field)
    netbox_vcpus BIGINT,                           -- vCPU sayısı
    netbox_memory BIGINT,                          -- Memory (MB)
    netbox_disk BIGINT,                            -- Disk (GB)

    -- Nutanix (Veri Toplama) Bilgileri
    nutanix_last_observed TIMESTAMPTZ,             -- Son güncel veri zamanı
    nutanix_status TEXT,                           -- Nutanix status
    nutanix_status_description TEXT,               -- Power durumu (Power:on / Power:off)
    nutanix_component_moid VARCHAR(255),           -- Component MOID
    nutanix_uuid VARCHAR(255),                     -- Nutanix UUID
    nutanix_guest_os VARCHAR(255),                 -- Guest OS
    nutanix_memory_mb BIGINT,                      -- Memory (MB)
    nutanix_num_vcpus INT,                         -- vCPU sayısı

    -- Hesaplanan Alanlar
    data_age_hours NUMERIC(10,2),                  -- Verinin yaşı (saat cinsinden), NULL ise hiç veri yok
    finding_type VARCHAR(20) NOT NULL,             -- 'STALE' veya 'MISSING'

    -- Metadata
    notes TEXT                                     -- Ek notlar
);

-- İndeksler
CREATE INDEX idx_monitoring_vm_check_time ON hmdl.hmdl_datalake_monitoring_vm(check_time);
CREATE INDEX idx_monitoring_vm_name ON hmdl.hmdl_datalake_monitoring_vm(netbox_vm_name);
CREATE INDEX idx_monitoring_vm_finding ON hmdl.hmdl_datalake_monitoring_vm(finding_type);

-- Yorum ekle
COMMENT ON TABLE hmdl.hmdl_datalake_monitoring_vm IS 'Datalake VM veri güncelliği monitoring sonuçları. Her scheduled çalışmada append edilir.';
COMMENT ON COLUMN hmdl.hmdl_datalake_monitoring_vm.finding_type IS 'STALE: Veri 1 haftadan eski, MISSING: Nutanix tablosunda hiç kayıt yok';
COMMENT ON COLUMN hmdl.hmdl_datalake_monitoring_vm.check_time IS 'Kontrolün çalıştırıldığı zaman damgası';
COMMENT ON COLUMN hmdl.hmdl_datalake_monitoring_vm.data_age_hours IS 'last_observed ile kontrol zamanı arasındaki fark (saat). MISSING durumunda NULL.';
