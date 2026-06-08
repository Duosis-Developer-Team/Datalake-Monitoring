# Datalake Monitoring - VM Veri Güncelliği Kontrolü

Netbox envanter tablosu ile Nutanix veri toplama tablosunu karşılaştırarak, güncel verisi gelmeyen VM'leri tespit eden monitoring aracı.

## Proje Yapısı

```
datalake-monitoring/
├── playbooks/
│   └── check_vm_data_freshness.yml   # AWX'in çalıştırdığı Ansible playbook
├── scripts/
│   └── check_vm_data_freshness.py    # Ana Python monitoring scripti
├── sql/
│   └── 001_create_hmdl_schema_and_tables.sql  # DDL (şema + wide tablo)
├── requirements.txt                  # Python bağımlılıkları
└── README.md
```

## Kurulum

### 1. Veritabanı Hazırlığı

`sql/001_create_hmdl_schema_and_tables.sql` dosyasını veritabanında çalıştırın:

```bash
psql -h <DB_HOST> -U <DB_USER> -d <DB_NAME> -f sql/001_create_hmdl_schema_and_tables.sql
```

### 2. AWX Yapılandırması

#### Adım 2.1 — Custom Credential Type Oluşturun

AWX → **Administration** → **Credential Types** → **Add**

**Name:** `Database Credential`

**Input Configuration:**
```yaml
fields:
  - id: db_host
    type: string
    label: Database Host
  - id: db_port
    type: string
    label: Database Port
    default: "5432"
  - id: db_name
    type: string
    label: Database Name
  - id: db_user
    type: string
    label: Database User
  - id: db_password
    type: string
    label: Database Password
    secret: true
required:
  - db_host
  - db_name
  - db_user
  - db_password
```

**Injector Configuration:**
```yaml
env:
  DB_HOST: "{{ db_host }}"
  DB_PORT: "{{ db_port }}"
  DB_NAME: "{{ db_name }}"
  DB_USER: "{{ db_user }}"
  DB_PASSWORD: "{{ db_password }}"
```

> Bu yapılandırma, credential'daki değerleri ortam değişkeni olarak playbook'a inject eder. Python scripti de bu ortam değişkenlerini okur.

#### Adım 2.2 — Credential Oluşturun

AWX → **Resources** → **Credentials** → **Add**

- **Name:** `Datalake DB Credential`
- **Credential Type:** `Database Credential` (yukarıda oluşturduğunuz)
- **Database Host / Port / Name / User / Password:** Bilgilerinizi girin

#### Adım 2.3 — Project Oluşturun

AWX → **Resources** → **Projects** → **Add**

- **Name:** `Datalake Monitoring`
- **Source Control Type:** `Git`
- **Source Control URL:** `https://github.com/<org>/<repo>.git`
- **Source Control Branch:** `main`
- **Execution Environment:** psycopg2'nin yüklü olduğu image

#### Adım 2.4 — Job Template Oluşturun

AWX → **Resources** → **Templates** → **Add** → **Add Job Template**

- **Name:** `VM Veri Güncelliği Kontrolü`
- **Job Type:** `Run`
- **Inventory:** `localhost` inventory (veya mevcut inventory'niz)
- **Project:** `Datalake Monitoring`
- **Playbook:** `playbooks/check_vm_data_freshness.yml`
- **Credentials:** `Datalake DB Credential`
- **Extra Variables:**
  ```yaml
  threshold_hours: 168
  dry_run: false
  ```

#### Adım 2.5 — Schedule Oluşturun (Opsiyonel)

Job Template → **Schedules** → **Add**

- **Name:** `Günlük VM Kontrol`
- **Run Every:** 1 Day (veya ihtiyacınıza göre)

## Akış Diyagramı

```
┌─────────────────────────────────────────────────────────────────┐
│                        AWX JOB TEMPLATE                        │
│  ┌──────────┐   ┌──────────────┐   ┌────────────────────────┐  │
│  │ Schedule  │──▶│   Project    │──▶│  Ansible Playbook      │  │
│  │ (cron)   │   │ (Git sync)   │   │  check_vm_...yml       │  │
│  └──────────┘   └──────────────┘   └───────────┬────────────┘  │
│                                                │               │
│  ┌──────────────────┐                          ▼               │
│  │   Credential     │──── env vars ──▶  Python Script          │
│  │ DB_HOST, DB_USER │                  check_vm_...py          │
│  │ DB_PASSWORD ...  │                          │               │
│  └──────────────────┘                          ▼               │
│                                    ┌───────────────────────┐   │
│  ┌──────────────────┐              │    PostgreSQL DB       │   │
│  │ Execution Env    │              │  ┌─────────────────┐  │   │
│  │ (psycopg2 image) │              │  │ netbox_vm (READ)│  │   │
│  └──────────────────┘              │  │ nutanix_vm(READ)│  │   │
│                                    │  │ hmdl_vm (WRITE) │  │   │
│                                    │  └─────────────────┘  │   │
│                                    └───────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Manuel Çalıştırma

```bash
# Ortam değişkenlerini set edin
export DB_HOST=your-db-host
export DB_PORT=5432
export DB_NAME=your-db-name
export DB_USER=your-db-user
export DB_PASSWORD=your-db-password

# Test modu (tabloya yazmaz)
python scripts/check_vm_data_freshness.py --dry-run

# Normal çalıştırma
python scripts/check_vm_data_freshness.py

# Farklı eşik (3 gün)
python scripts/check_vm_data_freshness.py --threshold-hours 72
```

## Sonuçları Sorgulama

```sql
-- Son kontrolün özeti
SELECT finding_type, COUNT(*)
FROM hmdl.hmdl_datalake_monitoring_vm
WHERE check_time = (SELECT MAX(check_time) FROM hmdl.hmdl_datalake_monitoring_vm)
GROUP BY finding_type;

-- Son 7 günün trend analizi
SELECT
    check_time::date AS kontrol_tarihi,
    finding_type,
    COUNT(*) AS vm_sayisi
FROM hmdl.hmdl_datalake_monitoring_vm
GROUP BY check_time::date, finding_type
ORDER BY kontrol_tarihi DESC;
```
