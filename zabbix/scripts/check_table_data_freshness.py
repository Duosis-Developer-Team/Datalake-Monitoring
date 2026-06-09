#!/usr/bin/env python3
"""
Datalake Monitoring - Tablo Veri Güncelliği (Zabbix Kaynaklı)

Zabbix 7.0 API'sinden db-master hostundaki "last data" tag'li item'ları
okuyarak, datalake tablolarının son veri tarihlerini
hmdl.hmdl_datalake_table_monitoring tablosuna UPSERT mantığıyla yazar.

Item adı formatı:
    Table [<tablo_adı>]: Last data timestamp (<kolon_adı>)
    Örn: Table [ibm_vios_general]: Last data timestamp (time)

Zabbix API akışı:
    1. user.login  → auth token
    2. host.get    → db-master host ID
    3. item.get    → "last data" tag'li item'ları çek (name, lastvalue, lastclock)
    4. Parse       → tablo adı, kolon adı, timestamp değeri ayıkla
    5. UPSERT      → hmdl.hmdl_datalake_table_monitoring tablosuna yaz

Kullanım:
    python check_table_data_freshness.py
    python check_table_data_freshness.py --dry-run
    python check_table_data_freshness.py --zabbix-host db-master

Ortam Değişkenleri:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
    ZABBIX_URL      : Zabbix frontend URL (ör: https://zabbix.example.com)
    ZABBIX_USER     : Zabbix API kullanıcısı
    ZABBIX_PASSWORD : Zabbix API şifresi
"""

import os
import sys
import re
import json
import argparse
import logging
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import execute_values
import urllib.request

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("datalake_monitoring_zabbix")

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------
DEFAULT_ZABBIX_HOST = "db-master"
ZABBIX_TAG_NAME = "last data"

# Item adı parse regex:  Table [ibm_vios_general]: Last data timestamp (time)
ITEM_NAME_PATTERN = re.compile(
    r'^Table\s+\[(.+?)\]:\s+Last\s+data\s+timestamp\s+\((.+?)\)$',
    re.IGNORECASE,
)

# UPSERT sorgusu: (table_name, column_name) UNIQUE key üzerinden
UPSERT_QUERY = """
INSERT INTO hmdl.hmdl_datalake_table_monitoring (
    table_name, column_name,
    zabbix_host, zabbix_item_id, zabbix_item_name,
    last_data_timestamp, data_age_hours,
    check_time, last_clock
) VALUES %s
ON CONFLICT (table_name, column_name)
DO UPDATE SET
    zabbix_host          = EXCLUDED.zabbix_host,
    zabbix_item_id       = EXCLUDED.zabbix_item_id,
    zabbix_item_name     = EXCLUDED.zabbix_item_name,
    last_data_timestamp  = EXCLUDED.last_data_timestamp,
    data_age_hours       = EXCLUDED.data_age_hours,
    check_time           = EXCLUDED.check_time,
    last_clock           = EXCLUDED.last_clock
"""

UPSERT_TEMPLATE = """(
    %(table_name)s, %(column_name)s,
    %(zabbix_host)s, %(zabbix_item_id)s, %(zabbix_item_name)s,
    %(last_data_timestamp)s, %(data_age_hours)s,
    %(check_time)s, %(last_clock)s
)"""


# ---------------------------------------------------------------------------
# Zabbix API
# ---------------------------------------------------------------------------

class ZabbixAPI:
    """Basit Zabbix 7.0 JSON-RPC istemcisi."""

    def __init__(self, url):
        self.url = url.rstrip("/") + "/api_jsonrpc.php"
        self.auth = None
        self._request_id = 0

    def _call(self, method, params=None):
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": self._request_id,
        }
        if self.auth:
            payload["auth"] = self.auth

        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
        except Exception as e:
            logger.error("Zabbix API hatası (%s): %s", method, e)
            raise

        if "error" in result:
            err = result["error"]
            raise RuntimeError(
                f"Zabbix API error [{err.get('code')}]: {err.get('message')} - {err.get('data')}"
            )
        return result.get("result")

    def login(self, user, password):
        self.auth = self._call("user.login", {"user": user, "password": password})
        logger.info("Zabbix API login başarılı.")

    def logout(self):
        if self.auth:
            try:
                self._call("user.logout")
            except Exception:
                pass
            self.auth = None

    def get_host_id(self, hostname):
        hosts = self._call("host.get", {
            "filter": {"host": hostname},
            "output": ["hostid", "host"],
        })
        if not hosts:
            raise RuntimeError(f"Zabbix'te '{hostname}' host bulunamadı.")
        return hosts[0]["hostid"]

    def get_items_by_tag(self, host_id, tag_name):
        items = self._call("item.get", {
            "hostids": host_id,
            "tags": [{"tag": tag_name}],
            "output": ["itemid", "name", "lastvalue", "lastclock"],
            "sortfield": "name",
        })
        return items or []


# ---------------------------------------------------------------------------
# Fonksiyonlar
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Datalake tablo veri güncelliği kontrolü (Zabbix kaynaklı)"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Sonuçları tabloya yazmadan gösterir")
    parser.add_argument("--zabbix-host", default=DEFAULT_ZABBIX_HOST,
                        help=f"Zabbix host adı (varsayılan: {DEFAULT_ZABBIX_HOST})")
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


def parse_item_name(item_name):
    """
    Item adından tablo ve kolon adını ayıklar.
    'Table [ibm_vios_general]: Last data timestamp (time)' → ('ibm_vios_general', 'time')
    """
    match = ITEM_NAME_PATTERN.match(item_name.strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return None, None


def parse_timestamp(value_str):
    """
    Zabbix item lastvalue'dan timestamp parse eder.
    Desteklenen formatlar:
        2026-06-10 12:55:00         → naive → UTC
        2026-06-10 12:55:00 AM/PM   → 12h format
        2026-06-10T12:55:00.000Z    → ISO format
        1718000100                   → epoch
    """
    if not value_str or value_str.strip() == "":
        return None

    value_str = value_str.strip()

    # Epoch (tamamen sayısal)
    if value_str.isdigit():
        try:
            return datetime.fromtimestamp(int(value_str), tz=timezone.utc)
        except Exception:
            pass

    # ISO formatı
    try:
        return datetime.fromisoformat(value_str.replace("Z", "+00:00"))
    except Exception:
        pass

    # AM/PM formatı: "2026-06-10 12:55:00 AM"
    for fmt in (
        "%Y-%m-%d %I:%M:%S %p",
        "%Y-%m-%d %H:%M:%S %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %I:%M:%S %p",
    ):
        try:
            dt = datetime.strptime(value_str, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    logger.warning("Timestamp parse edilemedi: '%s'", value_str)
    return None


def fetch_zabbix_items(zabbix_host):
    """Zabbix API'den db-master hostundaki 'last data' tag'li item'ları çeker."""
    zabbix_url = os.environ.get("ZABBIX_URL", "").rstrip("/")
    zabbix_user = os.environ.get("ZABBIX_USER", "")
    zabbix_password = os.environ.get("ZABBIX_PASSWORD", "")

    if not zabbix_url:
        logger.error("ZABBIX_URL ortam değişkeni tanımlı değil.")
        sys.exit(1)
    if not zabbix_user or not zabbix_password:
        logger.error("ZABBIX_USER / ZABBIX_PASSWORD ortam değişkenleri eksik.")
        sys.exit(1)

    api = ZabbixAPI(zabbix_url)

    try:
        api.login(zabbix_user, zabbix_password)

        host_id = api.get_host_id(zabbix_host)
        logger.info("Zabbix host bulundu: %s (ID: %s)", zabbix_host, host_id)

        items = api.get_items_by_tag(host_id, ZABBIX_TAG_NAME)
        logger.info("'%s' tag'li %d item bulundu.", ZABBIX_TAG_NAME, len(items))

        return items

    finally:
        api.logout()


def build_results(items, zabbix_host, check_time):
    """Zabbix item'larını parse edip tablo verilerine dönüştürür."""
    now = datetime.now(timezone.utc)
    results = []
    skipped = 0

    for item in items:
        item_name = item.get("name", "")
        table_name, column_name = parse_item_name(item_name)

        if not table_name:
            logger.debug("Item adı parse edilemedi, atlanıyor: %s", item_name)
            skipped += 1
            continue

        last_value = item.get("lastvalue", "")
        last_data_ts = parse_timestamp(last_value)

        # data_age_hours hesapla
        data_age_hours = None
        if last_data_ts:
            if last_data_ts.tzinfo is None:
                last_data_ts = last_data_ts.replace(tzinfo=timezone.utc)
            age_seconds = (now - last_data_ts).total_seconds()
            data_age_hours = round(age_seconds / 3600, 2)

        last_clock = item.get("lastclock")
        if last_clock:
            try:
                last_clock = int(last_clock)
            except (ValueError, TypeError):
                last_clock = None

        results.append({
            "table_name": table_name,
            "column_name": column_name,
            "zabbix_host": zabbix_host,
            "zabbix_item_id": item.get("itemid"),
            "zabbix_item_name": item_name,
            "last_data_timestamp": last_data_ts,
            "data_age_hours": data_age_hours,
            "check_time": check_time,
            "last_clock": last_clock,
        })

    if skipped:
        logger.info("Parse edilemeyen item sayısı: %d (atlandı)", skipped)

    return results


def save_results(conn, results):
    """UPSERT mantığıyla sonuçları tabloya yazar."""
    if not results:
        logger.info("Yazılacak sonuç yok.")
        return 0

    logger.info("%d kayıt hmdl.hmdl_datalake_table_monitoring tablosuna yazılıyor (UPSERT)...",
                len(results))
    try:
        with conn.cursor() as cur:
            execute_values(cur, UPSERT_QUERY, results,
                           template=UPSERT_TEMPLATE, page_size=200)
        conn.commit()
        logger.info("Kayıtlar başarıyla yazıldı (UPSERT).")
        return len(results)
    except psycopg2.Error as e:
        conn.rollback()
        logger.error("Kayıt yazma hatası: %s", e)
        raise


def print_summary(results):
    if not results:
        logger.info("Hiç sonuç yok.")
        return

    logger.info("=" * 80)
    logger.info("KONTROL SONUCU - DATALAKE TABLO İZLEME (Zabbix)")
    logger.info("=" * 80)
    logger.info("Toplam izlenen tablo: %d", len(results))
    logger.info("-" * 80)
    logger.info("  %-40s %-20s %s", "TABLO", "KOLON", "SON VERİ TARİHİ")
    logger.info("  %-40s %-20s %s", "-" * 40, "-" * 20, "-" * 25)

    # Yaşa göre sırala (en eski önce)
    sorted_results = sorted(
        results,
        key=lambda r: r.get("data_age_hours") or 0,
        reverse=True,
    )

    stale_count = 0
    for r in sorted_results:
        ts = r.get("last_data_timestamp")
        age = r.get("data_age_hours")
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "N/A"
        age_str = f"({age:.1f}h)" if age is not None else ""

        # 7 günden eski ise vurgula
        marker = ""
        if age and age > 168:
            marker = " ⚠️ STALE"
            stale_count += 1

        logger.info("  %-40s %-20s %s %s%s",
                    r["table_name"], r["column_name"], ts_str, age_str, marker)

    logger.info("-" * 80)
    if stale_count:
        logger.info("  ⚠️  %d tablo 7 günden uzun süredir güncel veri almamış!", stale_count)
    else:
        logger.info("  ✅ Tüm tabloların verisi güncel.")
    logger.info("=" * 80)


# ---------------------------------------------------------------------------
# Ana Akış
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    check_time = datetime.now(timezone.utc)

    logger.info("=" * 80)
    logger.info("Datalake Monitoring - Tablo Veri Güncelliği (Zabbix)")
    logger.info("Zabbix host      : %s", args.zabbix_host)
    logger.info("Zabbix tag       : %s", ZABBIX_TAG_NAME)
    logger.info("Başlangıç zamanı : %s", check_time.strftime("%Y-%m-%d %H:%M:%S UTC"))
    logger.info("Mod              : %s", "DRY-RUN" if args.dry_run else "NORMAL")
    logger.info("=" * 80)

    # 1. Zabbix'ten item'ları çek
    items = fetch_zabbix_items(args.zabbix_host)
    if not items:
        logger.warning("Zabbix'te '%s' tag'li item bulunamadı.", ZABBIX_TAG_NAME)
        sys.exit(0)

    # 2. Parse et
    results = build_results(items, args.zabbix_host, check_time)
    logger.info("Parse edilen item sayısı: %d", len(results))

    # 3. Özet
    print_summary(results)

    # 4. DB'ye yaz
    if args.dry_run:
        logger.info("DRY-RUN modu: Sonuçlar tabloya yazılmadı.")
    else:
        conn = get_db_connection()
        try:
            saved = save_results(conn, results)
            logger.info("Toplam %d kayıt UPSERT edildi.", saved)
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
