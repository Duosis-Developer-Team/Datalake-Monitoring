# Datalake Monitoring - VM Veri Güncelliği Kontrolü

Datalake ortamında veri kaybını tespit etmek için tasarlanmış monitoring aracı. Netbox envanter tablosu ile Nutanix veri toplama tablosunu UUID bazlı karşılaştırarak, güncel verisi gelmeyen VM'leri tespit eder ve sonuçları raporlama tablosuna yazar.

## Ne Yapar?

### Problem
Datalake ortamında birçok kaynaktan veri toplanmaktadır. Ancak bazı VM'lerin verisi zamanla gelmemeye başlayabilir (bağlantı kopması, agent sorunu, yapılandırma hatası vb.). Bu veri kayıplarının tespit edilmesi gerekmektedir.

### Çözüm
Netbox'taki **envanter bilgisi** (ne olması gerektiği) ile Nutanix'teki **veri toplama tablosu** (gerçekte ne toplandığı) karşılaştırılarak, verisi eksik veya güncel olmayan VM'ler tespit edilir.

### Karşılaştırılan Tablolar

| Tablo | Rolü | Açıklama |
|-------|------|----------|
| `discovery_netbox_virtualization_vm` | **Envanter (kaynak)** | Netbox'tan gelen tüm VM listesi. "Hangi VM'lerin verisi toplanmalı?" sorusunun cevabı |
| `discovery_nutanix_inventory_vm` | **Veri toplama** | Nutanix'ten toplanan VM verileri. "Hangi VM'lerin verisi gerçekten toplanıyor?" sorusunun cevabı |
| `hmdl.hmdl_datalake_monitoring_vm` | **Sonuç (wide tablo)** | Karşılaştırma sonucunda tespit edilen sorunlu VM'ler |

### Eşleştirme Mantığı

İki tablo arasındaki eşleştirme **UUID bazlı** yapılır:

```
Netbox: custom_fields_uuid  ←→  Nutanix: component_moid
```

Bu sayede:
- ✅ Aynı isimli farklı VM'ler birbirine karışmaz
- ✅ VM yeniden adlandırılsa bile eşleştirme bozulmaz
- ✅ Her VM birebir eşleşir (component_moid Nutanix'te UNIQUE)

### Filtreleme

Netbox tablosundaki tüm VM'ler kontrol edilmez. Sadece aşağıdaki koşulları sağlayanlar işleme alınır:

| Filtre | Kolon | Koşul | Açıklama |
|--------|-------|-------|----------|
| Platform | `tags1_display` | `LIKE '%Nutanix Acropolis%'` | Sadece Nutanix VM'leri (VMware hariç) |
| UUID | `custom_fields_uuid` | `IS NOT NULL` | UUID'si tanımlı olanlar |
| Duplicate | `custom_fields_uuid` | `DISTINCT ON` | Aynı UUID için en güncel kayıt |

### Tespit Edilen Durumlar

Script iki tür sorun tespit eder:

#### 🔴 MISSING — Nutanix'te Hiç Kaydı Yok
VM, Netbox envanterinde mevcut (Nutanix Acropolis etiketli) ama Nutanix veri toplama tablosunda hiçbir kaydı bulunmuyor.

**Olası sebepler:**
- VM yeni oluşturulmuş, henüz veri toplanmamış
- Veri toplama agent'ı bu VM'i hiç görmemiş
- UUID uyumsuzluğu

**Wide tablodaki görünümü:**
- `finding_type` = `MISSING`
- `nutanix_last_observed` = `NULL`
- `data_age_hours` = `NULL`

#### 🟡 STALE — Veri 1 Haftadan Eski
VM, hem Netbox'ta hem Nutanix'te mevcut, ancak Nutanix tablosundaki `last_observed` tarihi 1 haftadan (168 saat) daha eski.

**Olası sebepler:**
- Veri toplama pipeline'ı durmuş
- Network bağlantı sorunu
- Nutanix cluster erişim sorunu

**Wide tablodaki görünümü:**
- `finding_type` = `STALE`
- `nutanix_last_observed` = Son veri toplama tarihi (ör: `2026-05-30 14:00:00`)
- `data_age_hours` = Kaç saat geçtiği (ör: `250.5`)

#### ✅ Sağlıklı VM'ler — Tabloya Yazılmaz
Nutanix tablosundaki `last_observed` tarihi 7 gün içindeyse VM sağlıklı kabul edilir ve sonuç tablosuna **yazılmaz**.

### Kontrol Akışı

```
┌──────────────────────────────────────────────────────────────────────┐
│                         KONTROL AKIŞI                                │
│                                                                      │
│  ① Netbox tablosundan Nutanix Acropolis etiketli VM'leri çek        │
│     → tags1_display LIKE '%Nutanix Acropolis%'                       │
│     → custom_fields_uuid IS NOT NULL                                 │
│     → DISTINCT ON (custom_fields_uuid) — en güncel kayıt             │
│                                                                      │
│  ② LEFT JOIN ile Nutanix tablosuyla UUID bazlı eşleştir             │
│     → Netbox.custom_fields_uuid = Nutanix.component_moid            │
│                                                                      │
│  ③ Her VM için kontrol et:                                           │
│     ├─ Nutanix'te kayıt yok mu?          → MISSING                   │
│     ├─ last_observed > 7 gün eski mi?    → STALE                     │
│     └─ last_observed ≤ 7 gün içinde mi?  → Sağlıklı (yazılmaz)      │
│                                                                      │
│  ④ Sorunlu VM'leri hmdl.hmdl_datalake_monitoring_vm'ye yaz (append)  │
│                                                                      │
│  ⑤ Özet rapor yazdır (toplam / STALE / MISSING sayıları)            │
└──────────────────────────────────────────────────────────────────────┘
```

### Wide Tablo Kolonları

Sonuç tablosu (`hmdl.hmdl_datalake_monitoring_vm`) aşağıdaki bilgileri içerir:

| Grup | Kolon | Açıklama |
|------|-------|----------|
| **Kontrol** | `check_time` | Kontrolün çalıştırıldığı zaman |
| | `check_threshold_hours` | Kullanılan eşik (varsayılan: 168 saat) |
| **Netbox** | `netbox_vm_id` | Netbox VM ID |
| | `netbox_vm_name` | VM adı |
| | `netbox_site_name` | Site bilgisi |
| | `netbox_cluster_name` | Cluster bilgisi |
| | `netbox_status_value` | Netbox'taki power durumu |
| | `netbox_custom_fields_uuid` | UUID (eşleştirme anahtarı) |
| | `netbox_vcpus` / `netbox_memory` / `netbox_disk` | Kaynak bilgileri |
| **Nutanix** | `nutanix_last_observed` | **Son veri toplama tarihi** |
| | `nutanix_status` / `nutanix_status_description` | VM durumu |
| | `nutanix_component_moid` | Component MOID |
| | `nutanix_uuid` | Nutanix cluster UUID |
| | `nutanix_guest_os` | İşletim sistemi |
| | `nutanix_memory_mb` / `nutanix_num_vcpus` | Kaynak bilgileri |
| **Hesaplanan** | `data_age_hours` | Verinin yaşı (saat cinsinden) |
| | `finding_type` | `STALE` veya `MISSING` |

> Her scheduled çalışmada sonuçlar **append** edilir (eski sonuçlar silinmez). Bu sayede zaman içindeki trend analizi yapılabilir.

---

## Proje Yapısı

```
datalake-monitoring/
├── check_vm_data_freshness.yml       # AWX'in çalıştırdığı Ansible playbook
├── scripts/
│   └── check_vm_data_freshness.py    # Ana Python monitoring scripti
├── sql/
│   └── 001_create_hmdl_schema_and_tables.sql  # DDL (şema + wide tablo)
├── requirements.txt                  # Python bağımlılıkları (psycopg2)
└── README.md
```

| Dosya | Ne Yapar |
|-------|----------|
| `check_vm_data_freshness.yml` | AWX Job Template'in çalıştırdığı Ansible playbook. DB credential'larını env variable olarak Python scriptine geçirir. |
| `check_vm_data_freshness.py` | Ana monitoring scripti. DB'ye bağlanır, karşılaştırma sorgusunu çalıştırır, sonuçları wide tabloya yazar. |
| `001_create_hmdl_schema_and_tables.sql` | `hmdl` şemasını ve `hmdl_datalake_monitoring_vm` wide tablosunu oluşturan DDL. İlk kurulumda bir kez çalıştırılır. |

---

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
- **Update Revision on Launch:** ✅ (her çalıştırmada otomatik sync)

#### Adım 2.4 — Job Template Oluşturun

AWX → **Resources** → **Templates** → **Add** → **Add Job Template**

- **Name:** `VM Veri Güncelliği Kontrolü`
- **Job Type:** `Run`
- **Inventory:** `localhost` inventory
- **Project:** `Datalake Monitoring`
- **Playbook:** `check_vm_data_freshness.yml`
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

---

## AWX Akış Diyagramı

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

---

## Manuel Çalıştırma

```bash
# Ortam değişkenlerini set edin
export DB_HOST=your-db-host
export DB_PORT=5432
export DB_NAME=your-db-name
export DB_USER=your-db-user
export DB_PASSWORD=your-db-password

# Test modu (tabloya yazmaz, sadece sonuçları gösterir)
python scripts/check_vm_data_freshness.py --dry-run

# Normal çalıştırma (sonuçları tabloya yazar)
python scripts/check_vm_data_freshness.py

# Farklı eşik (3 gün)
python scripts/check_vm_data_freshness.py --threshold-hours 72
```

---

## Sonuçları Sorgulama

```sql
-- Son kontrolün özeti
SELECT finding_type, COUNT(*)
FROM hmdl.hmdl_datalake_monitoring_vm
WHERE check_time = (SELECT MAX(check_time) FROM hmdl.hmdl_datalake_monitoring_vm)
GROUP BY finding_type;

-- Son kontrolde tespit edilen STALE VM'ler (en eski veri önce)
SELECT netbox_vm_name, nutanix_last_observed, data_age_hours, netbox_cluster_name
FROM hmdl.hmdl_datalake_monitoring_vm
WHERE check_time = (SELECT MAX(check_time) FROM hmdl.hmdl_datalake_monitoring_vm)
  AND finding_type = 'STALE'
ORDER BY data_age_hours DESC;

-- Son 7 günün trend analizi
SELECT
    check_time::date AS kontrol_tarihi,
    finding_type,
    COUNT(*) AS vm_sayisi
FROM hmdl.hmdl_datalake_monitoring_vm
GROUP BY check_time::date, finding_type
ORDER BY kontrol_tarihi DESC;
```

---

## Gelecek Fazlar

| Faz | Katman | Kaynak Tablo | Envanter Tablo | Durum |
|-----|--------|-------------|----------------|-------|
| **1** | **VM** | `discovery_nutanix_inventory_vm` | `discovery_netbox_virtualization_vm` | ✅ Aktif |
| 2 | Host | TBD | TBD | 📋 Planlanıyor |
| 3 | Cluster | TBD | TBD | 📋 Planlanıyor |
| 4 | Uygulamalar | TBD | TBD | 📋 Planlanıyor |
