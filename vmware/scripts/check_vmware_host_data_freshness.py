#!/usr/bin/env python3
"""
Datalake Monitoring - VMware Host Veri Güncelliği Kontrolü

Netbox envanter tablosu (discovery_netbox_inventory_device) ile
VMware (discovery_vmware_inventory_host) ve Nutanix
(discovery_nutanix_inventory_host) veri toplama tabloları karşılaştırılarak
1 haftadan uzun süredir güncel verisi gelmeyen Host'lar tespit edilir.

Filtre (Netbox):
    tags1_display IN ('ESXI KLASIK HOST', 'ESXI KLASIK SAP HOST',
                      'ESXI NUTANIX', 'ESXI NUTANIX HOST',
                      'HYBRID', 'HYPER-V', 'KLASIK MIMARI')
    AND tenant_name = 'Bulutistan - Virtualization'

Eşleştirme:
    VMware:  name bazlı  (Netbox.name = VMware.name)
    Nutanix: serial bazlı (Netbox.serial = Nutanix.serial)

Tespit edilen sorunlar:
    STALE   : Her iki tablodaki en güncel last_observed 1 haftadan eski
    MISSING : Her iki tabloda da hiç kayıt yok

Sonuçlar hmdl.hmdl_datalake_monitoring_vmware_host tablosuna yazılır (append).

Kullanım:
    python check_vmware_host_data_freshness.py
    python check_vmware_host_data_freshness.py --dry-run
    python check_vmware_host_data_freshness.py --threshold-hours 72

Ortam Değişkenleri:
    DB_HOST, DB_PORT (varsayılan: 5432), DB_NAME, DB_USER, DB_PASSWORD
"""

import os
import sys
import argparse
import logging
from datetime import datetime

import psycopg2
from psycopg2.extras import execute_values

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("datalake_monitoring_vmware_host")

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLD_HOURS = 168  # 1 hafta

# Kabul edilen Netbox tag değerleri
ACCEPTED_TAGS = (
    'ESXI KLASIK HOST',
    'ESXI KLASIK SAP HOST',
    'ESXI NUTANIX',
    'ESXI NUTANIX HOST',
    'HYBRID',
    'HYPER-V',
    'KLASIK MIMARI',
)

# Netbox device + VMware host + Nutanix host karşılaştırma sorgusu
# VMware: name bazlı eşleştirme
# Nutanix: serial bazlı eşleştirme
# GREATEST ile iki kaynaktan en güncel last_observed alınır
DETECTION_QUERY = """
WITH netbox_hosts AS (
    SELECT DISTINCT ON (id)
        id, name, site_name, location_name, rack_name,
        status_value, serial, device_type_name,
        manufacturer_name, device_role_name, tenant_name,
        primary_ip_address, tags1_display,
        total_cores, total_ram, cpu
    FROM public.discovery_netbox_inventory_device
    WHERE tags1_display = ANY(%(accepted_tags)s)
      AND tenant_name = 'Bulutistan - Virtualization'
    ORDER BY id, last_updated DESC NULLS LAST
),
vmware_hosts AS (
    SELECT DISTINCT ON (name)
        name, last_observed, status, status_description,
        component_moid, vcenter_uuid, model, version
    FROM public.discovery_vmware_inventory_host
    ORDER BY name, last_observed DESC NULLS LAST
),
nutanix_hosts AS (
    SELECT DISTINCT ON (serial)
        name, last_observed, status, status_description,
        component_moid, nutanix_uuid, serial, model
    FROM public.discovery_nutanix_inventory_host
    WHERE serial IS NOT NULL
      AND serial != ''
    ORDER BY serial, last_observed DESC NULLS LAST
)
SELECT
    nb.id                           AS netbox_device_id,
    nb.name                         AS netbox_device_name,
    nb.site_name                    AS netbox_site_name,
    nb.location_name                AS netbox_location_name,
    nb.rack_name                    AS netbox_rack_name,
    nb.status_value                 AS netbox_status_value,
    nb.serial                       AS netbox_serial,
    nb.device_type_name             AS netbox_device_type_name,
    nb.manufacturer_name            AS netbox_manufacturer_name,
    nb.device_role_name             AS netbox_device_role_name,
    nb.tenant_name                  AS netbox_tenant_name,
    nb.primary_ip_address           AS netbox_primary_ip_address,
    nb.tags1_display                AS netbox_tags1_display,
    nb.total_cores                  AS netbox_total_cores,
    nb.total_ram                    AS netbox_total_ram,
    nb.cpu                          AS netbox_cpu,

    -- VMware bilgileri (name bazlı)
    vmw.last_observed               AS vmware_last_observed,
    vmw.status                      AS vmware_status,
    vmw.status_description          AS vmware_status_description,
    vmw.component_moid              AS vmware_component_moid,
    vmw.vcenter_uuid                AS vmware_vcenter_uuid,
    vmw.name                        AS vmware_name,
    vmw.model                       AS vmware_model,
    vmw.version                     AS vmware_version,

    -- Nutanix bilgileri (serial bazlı)
    nut.last_observed               AS nutanix_last_observed,
    nut.status                      AS nutanix_status,
    nut.status_description          AS nutanix_status_description,
    nut.component_moid              AS nutanix_component_moid,
    nut.nutanix_uuid                AS nutanix_uuid,
    nut.name                        AS nutanix_name,
    nut.serial                      AS nutanix_serial,
    nut.model                       AS nutanix_model,

    -- En güncel kaynak
    CASE
        WHEN vmw.last_observed IS NOT NULL AND nut.last_observed IS NOT NULL THEN
            CASE WHEN vmw.last_observed >= nut.last_observed THEN 'VMWARE' ELSE 'NUTANIX' END
        WHEN vmw.last_observed IS NOT NULL THEN 'VMWARE'
        WHEN nut.last_observed IS NOT NULL THEN 'NUTANIX'
        ELSE NULL
    END                             AS most_recent_source,

    GREATEST(
        vmw.last_observed,
        nut.last_observed
    )                               AS most_recent_observed,

    ROUND(
        EXTRACT(EPOCH FROM (
            NOW() - GREATEST(
                COALESCE(vmw.last_observed, '1970-01-01'::timestamptz),
                COALESCE(nut.last_observed, '1970-01-01'::timestamptz)
            )
        )) / 3600, 2
    )                               AS data_age_hours,

    CASE
        WHEN vmw.name IS NULL AND nut.serial IS NULL THEN 'MISSING'
        WHEN GREATEST(
                COALESCE(vmw.last_observed, '1970-01-01'::timestamptz),
                COALESCE(nut.last_observed, '1970-01-01'::timestamptz)
             ) < NOW() - make_interval(hours => %(threshold_hours)s) THEN 'STALE'
    END                             AS finding_type

FROM netbox_hosts nb
LEFT JOIN vmware_hosts vmw
    ON nb.name = vmw.name
LEFT JOIN nutanix_hosts nut
    ON nb.serial = nut.serial
WHERE (
    (vmw.name IS NULL AND nut.serial IS NULL)
    OR GREATEST(
        COALESCE(vmw.last_observed, '1970-01-01'::timestamptz),
        COALESCE(nut.last_observed, '1970-01-01'::timestamptz)
    ) < NOW() - make_interval(hours => %(threshold_hours)s)
)
ORDER BY
    CASE WHEN vmw.name IS NULL AND nut.serial IS NULL THEN 0 ELSE 1 END,
    data_age_hours DESC NULLS FIRST;
"""

INSERT_QUERY = """
INSERT INTO hmdl.hmdl_datalake_monitoring_vmware_host (
    check_time, check_threshold_hours,
    netbox_device_id, netbox_device_name, netbox_site_name,
    netbox_location_name, netbox_rack_name, netbox_status_value,
    netbox_serial, netbox_device_type_name, netbox_manufacturer_name,
    netbox_device_role_name, netbox_tenant_name, netbox_primary_ip_address,
    netbox_tags1_display, netbox_total_cores, netbox_total_ram, netbox_cpu,
    vmware_last_observed, vmware_status, vmware_status_description,
    vmware_component_moid, vmware_vcenter_uuid, vmware_name,
    vmware_model, vmware_version,
    nutanix_last_observed, nutanix_status, nutanix_status_description,
    nutanix_component_moid, nutanix_uuid, nutanix_name,
    nutanix_serial, nutanix_model,
    most_recent_source, most_recent_observed, data_age_hours, finding_type
) VALUES %s
"""

INSERT_TEMPLATE = """(
    %(check_time)s, %(check_threshold_hours)s,
    %(netbox_device_id)s, %(netbox_device_name)s, %(netbox_site_name)s,
    %(netbox_location_name)s, %(netbox_rack_name)s, %(netbox_status_value)s,
    %(netbox_serial)s, %(netbox_device_type_name)s, %(netbox_manufacturer_name)s,
    %(netbox_device_role_name)s, %(netbox_tenant_name)s, %(netbox_primary_ip_address)s,
    %(netbox_tags1_display)s, %(netbox_total_cores)s, %(netbox_total_ram)s, %(netbox_cpu)s,
    %(vmware_last_observed)s, %(vmware_status)s, %(vmware_status_description)s,
    %(vmware_component_moid)s, %(vmware_vcenter_uuid)s, %(vmware_name)s,
    %(vmware_model)s, %(vmware_version)s,
    %(nutanix_last_observed)s, %(nutanix_status)s, %(nutanix_status_description)s,
    %(nutanix_component_moid)s, %(nutanix_uuid)s, %(nutanix_name)s,
    %(nutanix_serial)s, %(nutanix_model)s,
    %(most_recent_source)s, %(most_recent_observed)s, %(data_age_hours)s, %(finding_type)s
)"""


# ---------------------------------------------------------------------------
# Fonksiyonlar
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Datalake VMware Host veri güncelliği kontrolü"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Sonuçları tabloya yazmadan gösterir")
    parser.add_argument("--threshold-hours", type=int, default=DEFAULT_THRESHOLD_HOURS,
                        help=f"Stale eşiği saat (varsayılan: {DEFAULT_THRESHOLD_HOURS})")
    return parser.parse_args()


def get_db_connection():
    required = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        logger.error("Eksik ortam değişkenleri: %s", ", ".join(missing))
        sys.exit(1)

    params = {
        "host": os.environ["DB_HOST"],
        "port": os.environ.get("DB_PORT", "5432"),
        "dbname": os.environ["DB_NAME"],
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
    }
    logger.info("Veritabanına bağlanılıyor: %s@%s:%s/%s",
                params["user"], params["host"], params["port"], params["dbname"])
    try:
        conn = psycopg2.connect(**params)
        conn.autocommit = False
        logger.info("Veritabanı bağlantısı başarılı.")
        return conn
    except psycopg2.Error as e:
        logger.error("Veritabanı bağlantı hatası: %s", e)
        sys.exit(1)


def find_stale_and_missing_hosts(conn, threshold_hours):
    logger.info(
        "VMware Host veri güncelliği kontrolü başlatılıyor (eşik: %d saat / %d gün)...",
        threshold_hours, threshold_hours // 24,
    )
    logger.info("Kabul edilen taglar: %s", ", ".join(ACCEPTED_TAGS))
    logger.info("Kontrol kaynakları: VMware (name) + Nutanix (serial)")

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            DETECTION_QUERY,
            {
                "threshold_hours": threshold_hours,
                "accepted_tags": list(ACCEPTED_TAGS),
            },
        )
        results = cur.fetchall()

    results = [dict(row) for row in results]

    stale_count = sum(1 for r in results if r["finding_type"] == "STALE")
    missing_count = sum(1 for r in results if r["finding_type"] == "MISSING")

    logger.info(
        "Kontrol tamamlandı. Toplam sorunlu VMware Host: %d (STALE: %d, MISSING: %d)",
        len(results), stale_count, missing_count,
    )
    return results


def save_results(conn, results, check_time, threshold_hours):
    if not results:
        logger.info("Yazılacak sonuç yok, tüm Host'ların verisi güncel.")
        return 0

    for row in results:
        row["check_time"] = check_time
        row["check_threshold_hours"] = threshold_hours

    logger.info(
        "%d kayıt hmdl.hmdl_datalake_monitoring_vmware_host tablosuna yazılıyor...",
        len(results),
    )
    try:
        with conn.cursor() as cur:
            execute_values(cur, INSERT_QUERY, results,
                           template=INSERT_TEMPLATE, page_size=500)
        conn.commit()
        logger.info("Kayıtlar başarıyla yazıldı.")
        return len(results)
    except psycopg2.Error as e:
        conn.rollback()
        logger.error("Kayıt yazma hatası: %s", e)
        raise


def print_summary(results):
    if not results:
        logger.info("=" * 70)
        logger.info("SONUÇ: Tüm VMware Host'ların verisi güncel.")
        logger.info("=" * 70)
        return

    stale = [r for r in results if r["finding_type"] == "STALE"]
    missing = [r for r in results if r["finding_type"] == "MISSING"]

    logger.info("=" * 70)
    logger.info("KONTROL SONUCU - VMWARE HOST (VMware + Nutanix)")
    logger.info("=" * 70)
    logger.info("Toplam sorunlu Host  : %d", len(results))
    logger.info("  STALE  (eski veri) : %d", len(stale))
    logger.info("  MISSING (veri yok) : %d", len(missing))
    logger.info("-" * 70)

    if missing:
        logger.info("")
        logger.info("MISSING Host'lar (VMware ve Nutanix'te hiç kaydı yok):")
        for host in missing[:20]:
            logger.info("  - %-40s  [serial: %s, tag: %s, site: %s]",
                        host["netbox_device_name"],
                        host.get("netbox_serial", "-"),
                        host.get("netbox_tags1_display", "-"),
                        host.get("netbox_site_name", "-"))
        if len(missing) > 20:
            logger.info("  ... ve %d Host daha", len(missing) - 20)

    if stale:
        logger.info("")
        logger.info("STALE Host'lar (en güncel veri bile 1 haftadan eski):")
        for host in stale[:20]:
            age_days = (
                round(float(host["data_age_hours"]) / 24, 1)
                if host["data_age_hours"] else "?"
            )
            logger.info("  - %-40s  [kaynak: %s, son veri: %s, yaş: %s gün]",
                        host["netbox_device_name"],
                        host.get("most_recent_source", "-"),
                        host.get("most_recent_observed", "-"),
                        age_days)
        if len(stale) > 20:
            logger.info("  ... ve %d Host daha", len(stale) - 20)

    logger.info("=" * 70)


# ---------------------------------------------------------------------------
# Ana Akış
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    check_time = datetime.now()

    logger.info("=" * 70)
    logger.info("Datalake Monitoring - VMware Host Veri Güncelliği Kontrolü")
    logger.info("Filtre           : %d farklı tag tipi", len(ACCEPTED_TAGS))
    logger.info("Kontrol kaynakları: VMware (name) + Nutanix (serial)")
    logger.info("Başlangıç zamanı : %s", check_time.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("Eşik             : %d saat (%d gün)", args.threshold_hours, args.threshold_hours // 24)
    logger.info("Mod              : %s", "DRY-RUN" if args.dry_run else "NORMAL")
    logger.info("=" * 70)

    conn = get_db_connection()

    try:
        results = find_stale_and_missing_hosts(conn, args.threshold_hours)
        print_summary(results)

        if args.dry_run:
            logger.info("DRY-RUN modu: Sonuçlar tabloya yazılmadı.")
        else:
            saved = save_results(conn, results, check_time, args.threshold_hours)
            logger.info("Toplam %d kayıt tabloya yazıldı.", saved)

    except Exception as e:
        logger.error("Beklenmeyen hata: %s", e, exc_info=True)
        sys.exit(1)
    finally:
        conn.close()
        logger.info("Veritabanı bağlantısı kapatıldı.")

    logger.info("İşlem tamamlandı.")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
