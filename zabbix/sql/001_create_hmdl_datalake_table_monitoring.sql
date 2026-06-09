-- ============================================================
-- Datalake Monitoring - Tablo Veri Güncelliği (Zabbix Kaynaklı)
-- ============================================================
-- Zabbix'ten alınan "Last data timestamp" item değerleri
-- ile datalake tablolarının son veri tarihlerini saklar.
--
-- UPSERT mantığıyla çalışır:
--   table_name + column_name birleşik UNIQUE key
--   Aynı tablo+kolon gelirse günceller, yoksa ekler.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS hmdl;

CREATE TABLE hmdl.hmdl_datalake_table_monitoring (
    id BIGSERIAL PRIMARY KEY,

    -- Tablo Bilgileri
    table_name TEXT NOT NULL,                       -- İzlenen tablo adı (ör: ibm_vios_general)
    column_name TEXT NOT NULL,                      -- İzlenen kolon adı (ör: time, timestamp, last_updated)

    -- Zabbix Bilgileri
    zabbix_host TEXT NOT NULL,                      -- Zabbix host adı (ör: db-master)
    zabbix_item_id TEXT,                            -- Zabbix item ID
    zabbix_item_name TEXT,                          -- Tam item adı (ör: Table [ibm_vios_general]: Last data timestamp (time))

    -- Veri Bilgileri
    last_data_timestamp TIMESTAMPTZ,                -- Tablodaki en güncel verinin tarihi (Zabbix'ten gelen değer)
    data_age_hours NUMERIC(10,2),                   -- Son veri ile kontrol zamanı arasındaki fark (saat)

    -- Kontrol Bilgileri
    check_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- Kontrolün yapıldığı zaman
    last_clock BIGINT,                              -- Zabbix item lastclock (epoch)

    -- UPSERT key: aynı tablo+kolon gelirse güncelle
    CONSTRAINT uq_table_column UNIQUE (table_name, column_name)
);

-- İndeksler
CREATE INDEX idx_table_monitoring_name       ON hmdl.hmdl_datalake_table_monitoring(table_name);
CREATE INDEX idx_table_monitoring_check_time ON hmdl.hmdl_datalake_table_monitoring(check_time);
CREATE INDEX idx_table_monitoring_age        ON hmdl.hmdl_datalake_table_monitoring(data_age_hours);

COMMENT ON TABLE hmdl.hmdl_datalake_table_monitoring IS
    'Datalake tablolarının son veri tarihlerini Zabbix üzerinden izler. Her çalıştırmada UPSERT ile güncellenir.';
COMMENT ON COLUMN hmdl.hmdl_datalake_table_monitoring.last_data_timestamp IS
    'Tablodaki en güncel verinin tarihi — Zabbix item''ından alınan değer';
COMMENT ON COLUMN hmdl.hmdl_datalake_table_monitoring.data_age_hours IS
    'Son veri tarihi ile kontrol zamanı arasındaki fark (saat cinsinden)';
