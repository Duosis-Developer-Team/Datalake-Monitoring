#!/usr/bin/env python3
"""
Datalake Monitoring - VMware VM Veri Güncelliği Kontrolü

Netbox envanter tablosu (discovery_netbox_virtualization_vm) ile
VMware veri toplama tablosu (discovery_vmware_inventory_vm) MOID bazlı
karşılaştırılarak 1 haftadan uzun süredir güncel verisi gelmeyen
VMware VM'leri tespit edilir.

Filtre:
    Netbox: tags1_display LIKE '%VMware%'
            AND custom_fields_moid IS NOT NULL

Eşleştirme: MOID bazlı
    Netbox.custom_fields_moid = VMware.component_moid

Tespit edilen sorunlar:
    STALE   : VMware tablosunda kayıt var ama last_observed 1 haftadan eski
    MISSING : Netbox'ta var ama VMware tablosunda hiç kaydı yok

Sonuçlar hmdl.hmdl_datalake_monitoring_vmware_vm tablosuna yazılır (append).

Kullanım:
    python check_vmware_vm_data_freshness.py
    python check_vmware_vm_data_freshness.py --dry-run
    python check_vmware_vm_data_freshness.py --threshold-hours 72

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
logger = logging.getLogger("datalake_monitoring_vmware_vm")

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLD_HOURS = 168  # 1 hafta

# Netbox VMware VM'leri ile VMware veri toplama tablosunu MOID bazlı karşılaştırır.
# Netbox'ta custom_fields_moid birden fazla kayıtta aynı değere sahip olabilir,
# DISTINCT ON ile en güncel kayıt alınır.
# VMware tablosunda component_moid UNIQUE olduğundan CTE gerekmez.
DETECTION_QUERY = """
WITH netbox_unique AS (
    SELECT DISTINCT ON (custom_fields_moid)
        id, name, site_name, cluster_name, status_value,
        custom_fields_moid, custom_fields_uuid,
        vcpus, memory, disk,
        custom_fields_guest_os,
        custom_fields_musteri,
        custom_fields_endpoint
    FROM public.discovery_netbox_virtualization_vm
    WHERE tags1_display LIKE '%%VMware%%'
      AND custom_fields_moid IS NOT NULL
      AND custom_fields_moid != ''
      AND status_value != 'poweredOff'
    ORDER BY custom_fields_moid, last_updated DESC NULLS LAST
)
SELECT
    nb.id                           AS netbox_vm_id,
    nb.name                         AS netbox_vm_name,
    nb.site_name                    AS netbox_site_name,
    nb.cluster_name                 AS netbox_cluster_name,
    nb.status_value                 AS netbox_status_value,
    nb.custom_fields_moid           AS netbox_custom_fields_moid,
    nb.custom_fields_uuid           AS netbox_custom_fields_uuid,
    nb.vcpus                        AS netbox_vcpus,
    nb.memory                       AS netbox_memory,
    nb.disk                         AS netbox_disk,
    nb.custom_fields_guest_os       AS netbox_guest_os,
    nb.custom_fields_musteri        AS netbox_musteri,
    nb.custom_fields_endpoint       AS netbox_endpoint,
    vmw.last_observed               AS vmware_last_observed,
    vmw.status                      AS vmware_status,
    vmw.status_description          AS vmware_status_description,
    vmw.component_moid              AS vmware_component_moid,
    vmw.vcenter_uuid                AS vmware_vcenter_uuid,
    vmw.name                        AS vmware_name,
    vmw.guest_os                    AS vmware_guest_os,
    vmw.tools_status                AS vmware_tools_status,
    ROUND(
        EXTRACT(EPOCH FROM (NOW() - vmw.last_observed)) / 3600, 2
    )                               AS data_age_hours,
    CASE
        WHEN vmw.component_moid IS NULL THEN 'MISSING'
        WHEN vmw.last_observed < NOW() - make_interval(hours => %(threshold_hours)s) THEN 'STALE'
    END                             AS finding_type
FROM netbox_unique nb
LEFT JOIN public.discovery_vmware_inventory_vm vmw
    ON nb.custom_fields_moid = vmw.component_moid
WHERE (
    vmw.component_moid IS NULL
    OR vmw.last_observed < NOW() - make_interval(hours => %(threshold_hours)s)
)
ORDER BY
    CASE WHEN vmw.component_moid IS NULL THEN 0 ELSE 1 END,
    data_age_hours DESC NULLS FIRST;
"""

INSERT_QUERY = """
INSERT INTO hmdl.hmdl_datalake_monitoring_vmware_vm (
    check_time,
    check_threshold_hours,
    netbox_vm_id,
    netbox_vm_name,
    netbox_site_name,
    netbox_cluster_name,
    netbox_status_value,
    netbox_custom_fields_moid,
    netbox_custom_fields_uuid,
    netbox_vcpus,
    netbox_memory,
    netbox_disk,
    netbox_guest_os,
    netbox_musteri,
    netbox_endpoint,
    vmware_last_observed,
    vmware_status,
    vmware_status_description,
    vmware_component_moid,
    vmware_vcenter_uuid,
    vmware_name,
    vmware_guest_os,
    vmware_tools_status,
    data_age_hours,
    finding_type
) VALUES %s
"""

INSERT_TEMPLATE = """(
    %(check_time)s,
    %(check_threshold_hours)s,
    %(netbox_vm_id)s,
    %(netbox_vm_name)s,
    %(netbox_site_name)s,
    %(netbox_cluster_name)s,
    %(netbox_status_value)s,
    %(netbox_custom_fields_moid)s,
    %(netbox_custom_fields_uuid)s,
    %(netbox_vcpus)s,
    %(netbox_memory)s,
    %(netbox_disk)s,
    %(netbox_guest_os)s,
    %(netbox_musteri)s,
    %(netbox_endpoint)s,
    %(vmware_last_observed)s,
    %(vmware_status)s,
    %(vmware_status_description)s,
    %(vmware_component_moid)s,
    %(vmware_vcenter_uuid)s,
    %(vmware_name)s,
    %(vmware_guest_os)s,
    %(vmware_tools_status)s,
    %(data_age_hours)s,
    %(finding_type)s
)"""


# ---------------------------------------------------------------------------
# Fonksiyonlar
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Datalake VMware VM veri güncelliği kontrolü"
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


def find_stale_and_missing_vms(conn, threshold_hours):
    logger.info(
        "VMware VM veri güncelliği kontrolü başlatılıyor (eşik: %d saat / %d gün)...",
        threshold_hours, threshold_hours // 24,
    )
    logger.info("Eşleştirme: Netbox.custom_fields_moid = VMware.component_moid")

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(DETECTION_QUERY, {"threshold_hours": threshold_hours})
        results = cur.fetchall()

    results = [dict(row) for row in results]

    stale_count = sum(1 for r in results if r["finding_type"] == "STALE")
    missing_count = sum(1 for r in results if r["finding_type"] == "MISSING")

    logger.info(
        "Kontrol tamamlandı. Toplam sorunlu VMware VM: %d (STALE: %d, MISSING: %d)",
        len(results), stale_count, missing_count,
    )
    return results


def save_results(conn, results, check_time, threshold_hours):
    if not results:
        logger.info("Yazılacak sonuç yok, tüm VMware VM'lerin verisi güncel.")
        return 0

    for row in results:
        row["check_time"] = check_time
        row["check_threshold_hours"] = threshold_hours

    logger.info("%d kayıt hmdl.hmdl_datalake_monitoring_vmware_vm tablosuna yazılıyor...",
                len(results))
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
        logger.info("SONUÇ: Tüm VMware VM'lerin verisi güncel.")
        logger.info("=" * 70)
        return

    stale = [r for r in results if r["finding_type"] == "STALE"]
    missing = [r for r in results if r["finding_type"] == "MISSING"]

    logger.info("=" * 70)
    logger.info("KONTROL SONUCU - VMWARE VM")
    logger.info("=" * 70)
    logger.info("Toplam sorunlu VM    : %d", len(results))
    logger.info("  STALE  (eski veri) : %d", len(stale))
    logger.info("  MISSING (veri yok) : %d", len(missing))
    logger.info("-" * 70)

    if missing:
        logger.info("")
        logger.info("MISSING VM'ler (VMware tablosunda hiç kaydı yok):")
        for vm in missing[:20]:
            logger.info("  - %-45s  [moid: %s, müşteri: %s]",
                        vm["netbox_vm_name"],
                        vm.get("netbox_custom_fields_moid", "-"),
                        vm.get("netbox_musteri", "-"))
        if len(missing) > 20:
            logger.info("  ... ve %d VM daha", len(missing) - 20)

    if stale:
        logger.info("")
        logger.info("STALE VM'ler (1 haftadan uzun süredir veri yok):")
        for vm in stale[:20]:
            age_days = (
                round(float(vm["data_age_hours"]) / 24, 1)
                if vm["data_age_hours"] else "?"
            )
            logger.info("  - %-45s  [son veri: %s, yaş: %s gün, müşteri: %s]",
                        vm["netbox_vm_name"],
                        vm.get("vmware_last_observed", "-"),
                        age_days,
                        vm.get("netbox_musteri", "-"))
        if len(stale) > 20:
            logger.info("  ... ve %d VM daha", len(stale) - 20)

    logger.info("=" * 70)


# ---------------------------------------------------------------------------
# Ana Akış
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    check_time = datetime.now()

    logger.info("=" * 70)
    logger.info("Datalake Monitoring - VMware VM Veri Güncelliği Kontrolü")
    logger.info("Filtre           : tags1_display LIKE '%%VMware%%'")
    logger.info("Eşleştirme       : custom_fields_moid = component_moid (MOID)")
    logger.info("Başlangıç zamanı : %s", check_time.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("Eşik             : %d saat (%d gün)", args.threshold_hours, args.threshold_hours // 24)
    logger.info("Mod              : %s", "DRY-RUN" if args.dry_run else "NORMAL")
    logger.info("=" * 70)

    conn = get_db_connection()

    try:
        results = find_stale_and_missing_vms(conn, args.threshold_hours)
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
