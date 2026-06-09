#!/usr/bin/env python3
"""
Datalake Monitoring - Cluster Veri Güncelliği Kontrolü

Netbox API'sinden (virtualization/clusters) çekilen cluster listesi
Nutanix (discovery_nutanix_inventory_cluster) ve VMware
(discovery_vmware_inventory_cluster) veri toplama tablolarıyla
karşılaştırılarak 1 haftadan uzun süredir güncel verisi gelmeyen
cluster'lar tespit edilir.

Envanter kaynağı: Netbox API (sayfalı)
    GET https://<NETBOX_HOST>/api/virtualization/clusters/?format=json
    Filtre: type.slug = 'acropolis-cls' (sadece Nutanix cluster'ları)

Eşleştirme: name bazlı
    API display/name = Nutanix.name = VMware.name

Tespit edilen sorunlar:
    STALE   : Her iki tablodaki en güncel last_observed 1 haftadan eski
    MISSING : Her iki tabloda da hiç kayıt yok

Sonuçlar hmdl.hmdl_datalake_monitoring_clusters tablosuna yazılır (append).

Kullanım:
    python check_cluster_data_freshness.py
    python check_cluster_data_freshness.py --dry-run
    python check_cluster_data_freshness.py --threshold-hours 72

Ortam Değişkenleri:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
    NETBOX_URL    : Netbox base URL (ör: https://loki.bulutistan.com)
    NETBOX_TOKEN  : Netbox API token
"""

import os
import sys
import argparse
import logging
from datetime import datetime

import psycopg2
from psycopg2.extras import execute_values, RealDictCursor
import urllib.request
import urllib.parse
import json

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("datalake_monitoring_cluster")

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLD_HOURS = 168       # 1 hafta
NETBOX_PAGE_SIZE = 50               # Netbox API sayfa boyutu
NETBOX_CLUSTER_TYPE_SLUG = "acropolis-cls"   # Sadece Nutanix cluster'ları

# DB'de Nutanix ve VMware cluster tablolarını karşılaştıran sorgu
# Envanter listesi Python tarafında API'den çekilir, bu sorgu tek cluster için çalışır
CLUSTER_LOOKUP_QUERY = """
WITH nutanix_clusters AS (
    SELECT DISTINCT ON (name)
        name, last_observed, status, status_description,
        component_moid, nutanix_uuid
    FROM public.discovery_nutanix_inventory_cluster
    ORDER BY name, last_observed DESC NULLS LAST
),
vmware_clusters AS (
    SELECT DISTINCT ON (name)
        name, last_observed, status, status_description,
        component_moid
    FROM public.discovery_vmware_inventory_cluster
    ORDER BY name, last_observed DESC NULLS LAST
)
SELECT
    nut.last_observed               AS nutanix_last_observed,
    nut.status                      AS nutanix_status,
    nut.status_description          AS nutanix_status_description,
    nut.component_moid              AS nutanix_component_moid,
    nut.nutanix_uuid                AS nutanix_uuid,
    nut.name                        AS nutanix_name,
    vmw.last_observed               AS vmware_last_observed,
    vmw.status                      AS vmware_status,
    vmw.status_description          AS vmware_status_description,
    vmw.component_moid              AS vmware_component_moid,
    vmw.name                        AS vmware_name
FROM (SELECT unnest(%(names)s::text[]) AS cluster_name) env
LEFT JOIN nutanix_clusters nut ON nut.name = env.cluster_name
LEFT JOIN vmware_clusters vmw  ON vmw.name = env.cluster_name
"""

INSERT_QUERY = """
INSERT INTO hmdl.hmdl_datalake_monitoring_clusters (
    check_time, check_threshold_hours,
    netbox_cluster_id, netbox_cluster_name, netbox_cluster_type, netbox_cluster_type_slug,
    nutanix_last_observed, nutanix_status, nutanix_status_description,
    nutanix_component_moid, nutanix_uuid, nutanix_name,
    vmware_last_observed, vmware_status, vmware_status_description,
    vmware_component_moid, vmware_name,
    most_recent_source, most_recent_observed, data_age_hours, finding_type
) VALUES %s
"""

INSERT_TEMPLATE = """(
    %(check_time)s, %(check_threshold_hours)s,
    %(netbox_cluster_id)s, %(netbox_cluster_name)s, %(netbox_cluster_type)s, %(netbox_cluster_type_slug)s,
    %(nutanix_last_observed)s, %(nutanix_status)s, %(nutanix_status_description)s,
    %(nutanix_component_moid)s, %(nutanix_uuid)s, %(nutanix_name)s,
    %(vmware_last_observed)s, %(vmware_status)s, %(vmware_status_description)s,
    %(vmware_component_moid)s, %(vmware_name)s,
    %(most_recent_source)s, %(most_recent_observed)s, %(data_age_hours)s, %(finding_type)s
)"""


# ---------------------------------------------------------------------------
# Fonksiyonlar
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Datalake Cluster veri güncelliği kontrolü"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Sonuçları tabloya yazmadan gösterir")
    parser.add_argument("--threshold-hours", type=int, default=DEFAULT_THRESHOLD_HOURS,
                        help=f"Stale eşiği (saat, varsayılan: {DEFAULT_THRESHOLD_HOURS})")
    return parser.parse_args()


def get_db_connection():
    """Ortam değişkenlerinden DB bağlantısı oluşturur."""
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


def fetch_netbox_clusters():
    """
    Netbox API'sinden tüm Nutanix cluster'larını sayfalı olarak çeker.
    Filtre: type__slug=acropolis-cls

    Returns:
        list[dict]: Her cluster için {id, name, type_display, type_slug}
    """
    netbox_url = os.environ.get("NETBOX_URL", "").rstrip("/")
    netbox_token = os.environ.get("NETBOX_TOKEN", "")

    if not netbox_url:
        logger.error("NETBOX_URL ortam değişkeni tanımlı değil.")
        sys.exit(1)
    if not netbox_token:
        logger.error("NETBOX_TOKEN ortam değişkeni tanımlı değil.")
        sys.exit(1)

    clusters = []
    url = (
        f"{netbox_url}/api/virtualization/clusters/"
        f"?format=json&limit={NETBOX_PAGE_SIZE}"
        f"&type__slug={NETBOX_CLUSTER_TYPE_SLUG}"
    )

    logger.info("Netbox API'den cluster listesi çekiliyor (filtre: %s)...",
                NETBOX_CLUSTER_TYPE_SLUG)

    while url:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Token {netbox_token}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            logger.error("Netbox API hatası (%s): %s", url, e)
            sys.exit(1)

        for item in data.get("results", []):
            clusters.append({
                "id": item.get("id"),
                "name": item.get("display") or item.get("name"),
                "type_display": item.get("type", {}).get("display"),
                "type_slug": item.get("type", {}).get("slug"),
            })

        url = data.get("next")  # Sonraki sayfa (None ise biter)

    logger.info("Netbox API: toplam %d cluster çekildi.", len(clusters))
    return clusters


def check_clusters_in_db(conn, cluster_names, threshold_hours):
    """
    Verilen cluster isimlerini DB'de Nutanix ve VMware tablolarında kontrol eder.

    Returns:
        dict: {cluster_name: {nutanix_last_observed, vmware_last_observed, ...}}
    """
    if not cluster_names:
        return {}

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(CLUSTER_LOOKUP_QUERY, {"names": cluster_names})
        rows = cur.fetchall()

    # Sıralama garanti değil, isim → satır eşlemesi yap
    # unnest sırası giriş sırasına göre gelir ama güvenli olmak için name ile eşleştir
    result = {}
    for i, name in enumerate(cluster_names):
        row = dict(rows[i]) if i < len(rows) else {}
        result[name] = row

    return result


def build_results(netbox_clusters, db_lookup, check_time, threshold_hours):
    """
    API'den gelen cluster listesi ve DB lookup sonuçlarını birleştirerek
    STALE/MISSING kayıtların listesini oluşturur.
    """
    from datetime import timezone

    now = datetime.now(timezone.utc)
    threshold_seconds = threshold_hours * 3600
    results = []

    for cluster in netbox_clusters:
        name = cluster["name"]
        row = db_lookup.get(name, {})

        nut_observed = row.get("nutanix_last_observed")
        vmw_observed = row.get("vmware_last_observed")

        # En güncel kaynağı belirle
        if nut_observed and vmw_observed:
            if nut_observed >= vmw_observed:
                most_recent_source = "NUTANIX"
                most_recent_observed = nut_observed
            else:
                most_recent_source = "VMWARE"
                most_recent_observed = vmw_observed
        elif nut_observed:
            most_recent_source = "NUTANIX"
            most_recent_observed = nut_observed
        elif vmw_observed:
            most_recent_source = "VMWARE"
            most_recent_observed = vmw_observed
        else:
            most_recent_source = None
            most_recent_observed = None

        # finding_type
        if most_recent_observed is None:
            finding_type = "MISSING"
            data_age_hours = None
        else:
            # timezone-aware karşılaştırma
            if most_recent_observed.tzinfo is None:
                from datetime import timezone as tz
                most_recent_observed = most_recent_observed.replace(tzinfo=tz.utc)
            age_seconds = (now - most_recent_observed).total_seconds()
            data_age_hours = round(age_seconds / 3600, 2)
            if age_seconds > threshold_seconds:
                finding_type = "STALE"
            else:
                finding_type = None  # Sağlıklı, yazılmaz

        if finding_type is None:
            continue

        results.append({
            "check_time": check_time,
            "check_threshold_hours": threshold_hours,
            "netbox_cluster_id": cluster["id"],
            "netbox_cluster_name": name,
            "netbox_cluster_type": cluster.get("type_display"),
            "netbox_cluster_type_slug": cluster.get("type_slug"),
            "nutanix_last_observed": nut_observed,
            "nutanix_status": row.get("nutanix_status"),
            "nutanix_status_description": row.get("nutanix_status_description"),
            "nutanix_component_moid": row.get("nutanix_component_moid"),
            "nutanix_uuid": row.get("nutanix_uuid"),
            "nutanix_name": row.get("nutanix_name"),
            "vmware_last_observed": vmw_observed,
            "vmware_status": row.get("vmware_status"),
            "vmware_status_description": row.get("vmware_status_description"),
            "vmware_component_moid": row.get("vmware_component_moid"),
            "vmware_name": row.get("vmware_name"),
            "most_recent_source": most_recent_source,
            "most_recent_observed": most_recent_observed,
            "data_age_hours": data_age_hours,
            "finding_type": finding_type,
        })

    return results


def save_results(conn, results):
    """Sonuçları hmdl.hmdl_datalake_monitoring_clusters tablosuna yazar."""
    if not results:
        logger.info("Yazılacak sonuç yok, tüm cluster'ların verisi güncel.")
        return 0

    logger.info("%d kayıt hmdl.hmdl_datalake_monitoring_clusters tablosuna yazılıyor...",
                len(results))
    try:
        with conn.cursor() as cur:
            execute_values(cur, INSERT_QUERY, results,
                           template=INSERT_TEMPLATE, page_size=200)
        conn.commit()
        logger.info("Kayıtlar başarıyla yazıldı.")
        return len(results)
    except psycopg2.Error as e:
        conn.rollback()
        logger.error("Kayıt yazma hatası: %s", e)
        raise


def print_summary(results):
    """Özet yazdırır."""
    if not results:
        logger.info("=" * 70)
        logger.info("SONUÇ: Tüm cluster'ların verisi güncel.")
        logger.info("=" * 70)
        return

    stale = [r for r in results if r["finding_type"] == "STALE"]
    missing = [r for r in results if r["finding_type"] == "MISSING"]

    logger.info("=" * 70)
    logger.info("KONTROL SONUCU - CLUSTER (Nutanix + VMware)")
    logger.info("=" * 70)
    logger.info("Toplam sorunlu Cluster : %d", len(results))
    logger.info("  STALE  (eski veri)   : %d", len(stale))
    logger.info("  MISSING (veri yok)   : %d", len(missing))
    logger.info("-" * 70)

    if missing:
        logger.info("")
        logger.info("MISSING Cluster'lar (Nutanix ve VMware'de hiç kaydı yok):")
        for c in missing[:20]:
            logger.info("  - %-40s  [type: %s]",
                        c["netbox_cluster_name"], c.get("netbox_cluster_type", "-"))
        if len(missing) > 20:
            logger.info("  ... ve %d cluster daha", len(missing) - 20)

    if stale:
        logger.info("")
        logger.info("STALE Cluster'lar (en güncel veri bile 1 haftadan eski):")
        for c in stale[:20]:
            age_days = round(float(c["data_age_hours"]) / 24, 1) if c["data_age_hours"] else "?"
            logger.info("  - %-40s  [kaynak: %s, son veri: %s, yaş: %s gün]",
                        c["netbox_cluster_name"],
                        c.get("most_recent_source", "-"),
                        c.get("most_recent_observed", "-"),
                        age_days)
        if len(stale) > 20:
            logger.info("  ... ve %d cluster daha", len(stale) - 20)

    logger.info("=" * 70)


# ---------------------------------------------------------------------------
# Ana Akış
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    check_time = datetime.now()

    logger.info("=" * 70)
    logger.info("Datalake Monitoring - Cluster Veri Güncelliği Kontrolü")
    logger.info("Envanter kaynağı : Netbox API (type: acropolis-cls)")
    logger.info("Kontrol kaynakları: Nutanix + VMware (name bazlı)")
    logger.info("Başlangıç zamanı : %s", check_time.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("Eşik             : %d saat (%d gün)", args.threshold_hours, args.threshold_hours // 24)
    logger.info("Mod              : %s", "DRY-RUN" if args.dry_run else "NORMAL")
    logger.info("=" * 70)

    # 1. Netbox API'den cluster listesini çek
    netbox_clusters = fetch_netbox_clusters()
    if not netbox_clusters:
        logger.warning("Netbox API'den hiç cluster gelmedi. Kontrol sonlandırıldı.")
        sys.exit(0)

    # 2. DB bağlantısı
    conn = get_db_connection()

    try:
        # 3. Tüm cluster isimlerini tek sorguda DB'de ara
        cluster_names = [c["name"] for c in netbox_clusters]
        logger.info("DB'de %d cluster kontrol ediliyor...", len(cluster_names))
        db_lookup = check_clusters_in_db(conn, cluster_names, args.threshold_hours)

        # 4. STALE / MISSING tespiti
        results = build_results(netbox_clusters, db_lookup, check_time, args.threshold_hours)

        stale_count = sum(1 for r in results if r["finding_type"] == "STALE")
        missing_count = sum(1 for r in results if r["finding_type"] == "MISSING")
        logger.info("Kontrol tamamlandı. Toplam sorunlu: %d (STALE: %d, MISSING: %d)",
                    len(results), stale_count, missing_count)

        # 5. Özet
        print_summary(results)

        # 6. Yaz
        if args.dry_run:
            logger.info("DRY-RUN modu: Sonuçlar tabloya yazılmadı.")
        else:
            saved = save_results(conn, results)
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
