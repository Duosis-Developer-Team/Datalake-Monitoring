-- ============================================================
-- Datalake Monitoring - Cluster Veri Güncelliği Kontrol Tablosu
-- ============================================================
-- Netbox API'sinden (virtualization/clusters) çekilen cluster listesi
-- ile Nutanix (discovery_nutanix_inventory_cluster) ve VMware
-- (discovery_vmware_inventory_cluster) veri toplama tabloları
-- karşılaştırılarak sonuçlar saklanır.
--
-- Eşleştirme: name bazlı (API display = Nutanix/VMware name)
--
-- finding_type:
--   STALE   = Her iki tabloda da last_observed 1 haftadan eski
--   MISSING = Her iki tabloda da hiç kaydı yok
-- ============================================================

CREATE SCHEMA IF NOT EXISTS hmdl;

CREATE TABLE hmdl.hmdl_datalake_monitoring_clusters (
    id BIGSERIAL PRIMARY KEY,

    -- Kontrol Bilgileri
    check_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    check_threshold_hours INT NOT NULL DEFAULT 168,

    -- Netbox API (Envanter) Bilgileri
    netbox_cluster_id INT,
    netbox_cluster_name TEXT NOT NULL,       -- API'den gelen display/name değeri
    netbox_cluster_type TEXT,               -- type.display (ör: "Acropolis CLS")
    netbox_cluster_type_slug TEXT,          -- type.slug

    -- Nutanix (Veri Toplama) Bilgileri
    nutanix_last_observed TIMESTAMPTZ,
    nutanix_status TEXT,
    nutanix_status_description TEXT,
    nutanix_component_moid VARCHAR(255),
    nutanix_uuid VARCHAR(255),
    nutanix_name TEXT,

    -- VMware (Veri Toplama) Bilgileri
    vmware_last_observed TIMESTAMPTZ,
    vmware_status TEXT,
    vmware_status_description TEXT,
    vmware_component_moid VARCHAR(255),
    vmware_name TEXT,

    -- Hesaplanan Alanlar
    most_recent_source VARCHAR(10),         -- 'NUTANIX' veya 'VMWARE'
    most_recent_observed TIMESTAMPTZ,       -- İki kaynaktan en güncel last_observed
    data_age_hours NUMERIC(10,2),
    finding_type VARCHAR(20) NOT NULL,      -- 'STALE' veya 'MISSING'

    -- Metadata
    notes TEXT
);

-- İndeksler
CREATE INDEX idx_monitoring_clusters_check_time  ON hmdl.hmdl_datalake_monitoring_clusters(check_time);
CREATE INDEX idx_monitoring_clusters_name        ON hmdl.hmdl_datalake_monitoring_clusters(netbox_cluster_name);
CREATE INDEX idx_monitoring_clusters_finding     ON hmdl.hmdl_datalake_monitoring_clusters(finding_type);

COMMENT ON TABLE hmdl.hmdl_datalake_monitoring_clusters IS
    'Datalake Cluster veri güncelliği monitoring sonuçları. Netbox API kaynaklı cluster listesi Nutanix ve VMware tablolarıyla karşılaştırılır.';
COMMENT ON COLUMN hmdl.hmdl_datalake_monitoring_clusters.most_recent_source IS
    'En güncel verinin kaynağı: NUTANIX veya VMWARE';
COMMENT ON COLUMN hmdl.hmdl_datalake_monitoring_clusters.most_recent_observed IS
    'Nutanix ve VMware last_observed değerlerinden en güncel olanı';
