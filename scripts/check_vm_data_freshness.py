#!/usr/bin/env python3
"""
Datalake Monitoring - VM Veri Güncelliği Kontrolü (Faz 1: Nutanix VM)

Netbox envanter tablosu (discovery_netbox_virtualization_vm) ile
Nutanix veri toplama tablosu (discovery_nutanix_inventory_vm) karşılaştırılarak
1 haftadan uzun süredir güncel verisi gelmeyen VM'ler tespit edilir.

Tespit edilen sorunlar:
    STALE   : Nutanix'te kayıt var ama last_observed 1 haftadan (168 saat) eski
    MISSING : Netbox'ta var ama Nutanix tablosunda hiç kaydı yok

Sonuçlar hmdl.hmdl_datalake_monitoring_vm tablosuna yazılır (append mode).

Kullanım:
    python check_vm_data_freshness.py
    python check_vm_data_freshness.py --dry-run
    python check_vm_data_freshness.py --threshold-hours 72

Ortam Değişkenleri:
    DB_HOST     : Veritabanı sunucu adresi
    DB_PORT     : Veritabanı portu (varsayılan: 5432)
    DB_NAME     : Veritabanı adı
    DB_USER     : Veritabanı kullanıcısı
    DB_PASSWORD : Veritabanı şifresi
"""

import os
import sys
import argparse
import logging
from datetime import datetime

import psycopg2
from psycopg2.extras import execute_values

# ---------------------------------------------------------------------------
# Logging Yapılandırması
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("datalake_monitoring")

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLD_HOURS = 168  # 1 hafta

# Netbox ve Nutanix tablolarını karşılaştıran ana sorgu
# CTE'ler ile her iki tabloda da name bazında deduplicate yapılır
DETECTION_QUERY = """
WITH netbox_unique AS (
    SELECT DISTINCT ON (name)
        id, name, site_name, cluster_name, status_value,
        custom_fields_uuid, custom_fields_moid,
        vcpus, memory, disk
    FROM public.discovery_netbox_virtualization_vm
    WHERE tags1_display LIKE '%%Nutanix Acropolis%%'
    ORDER BY name, last_updated DESC NULLS LAST
),
nutanix_unique AS (
    SELECT DISTINCT ON (name)
        name, last_observed, status, status_description,
        component_moid, nutanix_uuid, guest_os,
        memory_mb, num_vcpus
    FROM public.discovery_nutanix_inventory_vm
    ORDER BY name, last_observed DESC NULLS LAST
)
SELECT
    nb.id                           AS netbox_vm_id,
    nb.name                         AS netbox_vm_name,
    nb.site_name                    AS netbox_site_name,
    nb.cluster_name                 AS netbox_cluster_name,
    nb.status_value                 AS netbox_status_value,
    nb.custom_fields_uuid           AS netbox_custom_fields_uuid,
    nb.custom_fields_moid           AS netbox_custom_fields_moid,
    nb.vcpus                        AS netbox_vcpus,
    nb.memory                       AS netbox_memory,
    nb.disk                         AS netbox_disk,
    nut.last_observed               AS nutanix_last_observed,
    nut.status                      AS nutanix_status,
    nut.status_description          AS nutanix_status_description,
    nut.component_moid              AS nutanix_component_moid,
    nut.nutanix_uuid                AS nutanix_uuid,
    nut.guest_os                    AS nutanix_guest_os,
    nut.memory_mb                   AS nutanix_memory_mb,
    nut.num_vcpus                   AS nutanix_num_vcpus,
    ROUND(
        EXTRACT(EPOCH FROM (NOW() - nut.last_observed)) / 3600, 2
    )                               AS data_age_hours,
    CASE
        WHEN nut.name IS NULL THEN 'MISSING'
        WHEN nut.last_observed < NOW() - make_interval(hours => %(threshold_hours)s) THEN 'STALE'
    END                             AS finding_type
FROM netbox_unique nb
LEFT JOIN nutanix_unique nut
    ON nb.name = nut.name
WHERE (
    nut.name IS NULL
    OR nut.last_observed < NOW() - make_interval(hours => %(threshold_hours)s)
)
ORDER BY
    CASE WHEN nut.name IS NULL THEN 0 ELSE 1 END,
    data_age_hours DESC NULLS FIRST;
"""

# Wide tabloya INSERT sorgusu
INSERT_QUERY = """
INSERT INTO hmdl.hmdl_datalake_monitoring_vm (
    check_time,
    check_threshold_hours,
    netbox_vm_id,
    netbox_vm_name,
    netbox_site_name,
    netbox_cluster_name,
    netbox_status_value,
    netbox_custom_fields_uuid,
    netbox_custom_fields_moid,
    netbox_vcpus,
    netbox_memory,
    netbox_disk,
    nutanix_last_observed,
    nutanix_status,
    nutanix_status_description,
    nutanix_component_moid,
    nutanix_uuid,
    nutanix_guest_os,
    nutanix_memory_mb,
    nutanix_num_vcpus,
    data_age_hours,
    finding_type
) VALUES %s
"""

# INSERT için değer şablonu (execute_values ile kullanılır)
INSERT_TEMPLATE = """(
    %(check_time)s,
    %(check_threshold_hours)s,
    %(netbox_vm_id)s,
    %(netbox_vm_name)s,
    %(netbox_site_name)s,
    %(netbox_cluster_name)s,
    %(netbox_status_value)s,
    %(netbox_custom_fields_uuid)s,
    %(netbox_custom_fields_moid)s,
    %(netbox_vcpus)s,
    %(netbox_memory)s,
    %(netbox_disk)s,
    %(nutanix_last_observed)s,
    %(nutanix_status)s,
    %(nutanix_status_description)s,
    %(nutanix_component_moid)s,
    %(nutanix_uuid)s,
    %(nutanix_guest_os)s,
    %(nutanix_memory_mb)s,
    %(nutanix_num_vcpus)s,
    %(data_age_hours)s,
    %(finding_type)s
)"""


# ---------------------------------------------------------------------------
# Fonksiyonlar
# ---------------------------------------------------------------------------

def parse_args():
    """Komut satırı argümanlarını ayrıştırır."""
    parser = argparse.ArgumentParser(
        description="Datalake VM veri güncelliği kontrolü"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Sonuçları tabloya yazmadan sadece tespit edilen VM'leri gösterir",
    )
    parser.add_argument(
        "--threshold-hours",
        type=int,
        default=DEFAULT_THRESHOLD_HOURS,
        help=f"Stale kabul edilecek eşik saat (varsayılan: {DEFAULT_THRESHOLD_HOURS})",
    )
    return parser.parse_args()


def get_db_connection():
    """
    Ortam değişkenlerinden veritabanı bağlantısı oluşturur.

    Gerekli ortam değişkenleri:
        DB_HOST, DB_PORT (varsayılan 5432), DB_NAME, DB_USER, DB_PASSWORD
    """
    required_vars = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing = [var for var in required_vars if not os.environ.get(var)]
    if missing:
        logger.error(
            "Eksik ortam değişkenleri: %s", ", ".join(missing)
        )
        sys.exit(1)

    conn_params = {
        "host": os.environ["DB_HOST"],
        "port": os.environ.get("DB_PORT", "5432"),
        "dbname": os.environ["DB_NAME"],
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
    }

    logger.info(
        "Veritabanına bağlanılıyor: %s@%s:%s/%s",
        conn_params["user"],
        conn_params["host"],
        conn_params["port"],
        conn_params["dbname"],
    )

    try:
        conn = psycopg2.connect(**conn_params)
        conn.autocommit = False
        logger.info("Veritabanı bağlantısı başarılı.")
        return conn
    except psycopg2.Error as e:
        logger.error("Veritabanı bağlantı hatası: %s", e)
        sys.exit(1)


def find_stale_and_missing_vms(conn, threshold_hours):
    """
    Netbox envanterindeki VM'leri Nutanix veri toplama tablosuyla
    karşılaştırarak STALE ve MISSING VM'leri tespit eder.

    Args:
        conn: psycopg2 veritabanı bağlantısı
        threshold_hours: Stale kabul edilecek eşik (saat)

    Returns:
        list[dict]: Tespit edilen VM'lerin listesi
    """
    logger.info(
        "VM veri güncelliği kontrolü başlatılıyor (eşik: %d saat / %d gün)...",
        threshold_hours,
        threshold_hours // 24,
    )

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(DETECTION_QUERY, {"threshold_hours": threshold_hours})
        results = cur.fetchall()

    # dict'e çevir (RealDictRow -> dict)
    results = [dict(row) for row in results]

    stale_count = sum(1 for r in results if r["finding_type"] == "STALE")
    missing_count = sum(1 for r in results if r["finding_type"] == "MISSING")

    logger.info(
        "Kontrol tamamlandı. Toplam sorunlu VM: %d (STALE: %d, MISSING: %d)",
        len(results),
        stale_count,
        missing_count,
    )

    return results


def save_results(conn, results, check_time, threshold_hours):
    """
    Tespit edilen VM'leri hmdl.hmdl_datalake_monitoring_vm tablosuna yazar.

    Args:
        conn: psycopg2 veritabanı bağlantısı
        results: Tespit edilen VM listesi (dict listesi)
        check_time: Kontrolün yapıldığı zaman
        threshold_hours: Kullanılan eşik
    """
    if not results:
        logger.info("Yazılacak sonuç yok, tüm VM'lerin verisi güncel.")
        return 0

    # Her kayda check_time ve threshold ekle
    for row in results:
        row["check_time"] = check_time
        row["check_threshold_hours"] = threshold_hours

    logger.info(
        "%d kayıt hmdl.hmdl_datalake_monitoring_vm tablosuna yazılıyor...",
        len(results),
    )

    try:
        with conn.cursor() as cur:
            execute_values(
                cur,
                INSERT_QUERY,
                results,
                template=INSERT_TEMPLATE,
                page_size=500,
            )
        conn.commit()
        logger.info("Kayıtlar başarıyla yazıldı.")
        return len(results)
    except psycopg2.Error as e:
        conn.rollback()
        logger.error("Kayıt yazma hatası: %s", e)
        raise


def print_summary(results):
    """Tespit edilen VM'lerin özetini konsola yazdırır."""
    if not results:
        logger.info("=" * 60)
        logger.info("SONUÇ: Tüm VM'lerin verisi güncel. Sorun tespit edilmedi.")
        logger.info("=" * 60)
        return

    stale = [r for r in results if r["finding_type"] == "STALE"]
    missing = [r for r in results if r["finding_type"] == "MISSING"]

    logger.info("=" * 60)
    logger.info("KONTROL SONUCU")
    logger.info("=" * 60)
    logger.info("Toplam sorunlu VM   : %d", len(results))
    logger.info("  STALE  (eski veri): %d", len(stale))
    logger.info("  MISSING (veri yok): %d", len(missing))
    logger.info("-" * 60)

    if missing:
        logger.info("")
        logger.info("MISSING VM'ler (Nutanix'te hiç kaydı yok):")
        for vm in missing[:20]:  # İlk 20'sini göster
            logger.info(
                "  - %-40s  [site: %s, cluster: %s, status: %s]",
                vm["netbox_vm_name"],
                vm.get("netbox_site_name", "-"),
                vm.get("netbox_cluster_name", "-"),
                vm.get("netbox_status_value", "-"),
            )
        if len(missing) > 20:
            logger.info("  ... ve %d VM daha", len(missing) - 20)

    if stale:
        logger.info("")
        logger.info("STALE VM'ler (1 haftadan uzun süredir veri yok):")
        for vm in stale[:20]:  # İlk 20'sini göster
            age_days = (
                round(float(vm["data_age_hours"]) / 24, 1)
                if vm["data_age_hours"]
                else "?"
            )
            logger.info(
                "  - %-40s  [son veri: %s, yaş: %s gün]",
                vm["netbox_vm_name"],
                vm.get("nutanix_last_observed", "-"),
                age_days,
            )
        if len(stale) > 20:
            logger.info("  ... ve %d VM daha", len(stale) - 20)

    logger.info("=" * 60)


# ---------------------------------------------------------------------------
# Ana Akış
# ---------------------------------------------------------------------------

def main():
    """Ana çalışma akışı."""
    args = parse_args()
    check_time = datetime.now()

    logger.info("=" * 60)
    logger.info("Datalake Monitoring - VM Veri Güncelliği Kontrolü")
    logger.info("Başlangıç zamanı : %s", check_time.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("Eşik             : %d saat (%d gün)", args.threshold_hours, args.threshold_hours // 24)
    logger.info("Mod              : %s", "DRY-RUN" if args.dry_run else "NORMAL")
    logger.info("=" * 60)

    # 1. Veritabanı bağlantısı
    conn = get_db_connection()

    try:
        # 2. STALE ve MISSING VM'leri tespit et
        results = find_stale_and_missing_vms(conn, args.threshold_hours)

        # 3. Özet yazdır
        print_summary(results)

        # 4. Sonuçları tabloya yaz
        if args.dry_run:
            logger.info("DRY-RUN modu: Sonuçlar tabloya yazılmadı.")
        else:
            saved_count = save_results(conn, results, check_time, args.threshold_hours)
            logger.info("Toplam %d kayıt tabloya yazıldı.", saved_count)

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
