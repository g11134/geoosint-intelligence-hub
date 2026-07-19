# Organizations API

This API is a read-only layer over the organizations SQLite database:

- source database: `data/output/organizations.db`
- default database build source: `data/output/result.csv`
- enriched database build source: `data/output/enriched_result.csv`
- source longitude column: `coordinates_0`
- source latitude column: `coordinates_1`

The parser pipeline, queue, state files, raw JSONL and Excel exports are not changed by the API.

## Install

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Build Organizations DB

After the parser/export pipeline updates `data/output/result.csv`, rebuild the API database:

```powershell
python scripts/build_organizations_db.py
```

This creates `data/output/organizations.db` as a read-model for Flutter/API access.
It does not modify `data/state/seen_ids.db`, `data/state/parsing_queue.csv`, `data/raw/raw_data.jsonl`, CSV or Excel exports.

To build the enriched Flutter read-model, use `enriched_result.csv` as the source:

```powershell
python scripts/build_organizations_db.py --source data/output/enriched_result.csv
```

For snapshot runs, point `YANDEX_SCRAPER_DATA_DIR` at the run folder first:

```powershell
$env:YANDEX_SCRAPER_DATA_DIR="data/runs/2026-04/стоматология"
python scripts/build_organizations_db.py --source "$env:YANDEX_SCRAPER_DATA_DIR/output/enriched_result.csv"
```

When the source is enriched, the database keeps the legacy `organizations` table and adds:

- `organization_cards`: one Flutter-oriented row per organization.
- `organization_features`: normalized feature values from `raw_features_json`.
- `organization_categories`: normalized values from `raw_categories_json`.

## Run Locally

```powershell
python scripts/run_api.py
```

Default base URL:

```text
http://127.0.0.1:8000
```

For access from another device on the same network:

```powershell
python scripts/run_api.py --host 0.0.0.0 --port 8000
```

Then use your computer LAN IP from Flutter, for example:

```text
http://192.168.1.10:8000
```

## Endpoints

### Health

```http
GET /health
```

Returns whether `data/output/organizations.db` exists and how many valid coordinate records are available.

### Metadata

```http
GET /api/meta
```

Returns source database metadata, total API-visible record count and category counts.

### Flutter-friendly JSON

```http
GET /api/organizations
```

Query parameters:

- `q`: optional text search in title, address, category, phone and source query
- `category`: optional category substring
- `bbox`: optional `lon_min,lat_min,lon_max,lat_max`
- `limit`: default `5000`, max `50000`
- `offset`: default `0`

Example:

```http
GET /api/organizations?limit=100
```

Response shape:

```json
{
  "items": [
    {
      "id": "126736325393",
      "title": "Example",
      "fullAddress": "Saint Petersburg",
      "categories": "Coffee shop",
      "phones_0_number": "+7...",
      "ratingData_ratingValue": "4.70",
      "ratingData_ratingCount": 673,
      "lat": 59.788392,
      "lon": 30.14371
    }
  ],
  "count": 1,
  "total": 1,
  "limit": 5000,
  "offset": 0
}
```

### GeoJSON

```http
GET /api/organizations.geojson
```

GeoJSON coordinate order is standard GeoJSON order:

```json
[lon, lat]
```

Use this endpoint for Leaflet, Mapbox, GIS tools, or Flutter packages that support GeoJSON.

### Enriched Flutter JSON

```http
GET /api/v2/organizations
```

Query parameters:

- `q`: optional search in card fields and enriched feature lists
- `category`: optional category substring
- `bbox`: optional `lon_min,lat_min,lon_max,lat_max`
- `service`: optional service id/name substring, for example `caries_treatment`
- `payment`: optional payment method id/name substring, for example `installment`
- `specialist`: optional specialist id/name substring, for example `implantologist`
- `limit`: default `5000`, max `50000`
- `offset`: default `0`

Example:

```http
GET /api/v2/organizations?service=implantology&payment=installment&limit=100
```

Response items are one card per organization:

```json
{
  "items": [
    {
      "id": "84369752236",
      "title": "NDenta",
      "fullAddress": "Saint Petersburg",
      "category": "Dental clinic",
      "categories": [{"id": "184106132", "name": "Dental clinic"}],
      "phone": "+7...",
      "websiteUrl": "https://example.com",
      "lat": 60.0,
      "lon": 30.0,
      "rating": {
        "value": 5.0,
        "count": 68,
        "reviewCount": 54
      },
      "features": {
        "services": [{"id": "implantology", "name": "implantology"}],
        "paymentMethods": [{"id": "installment", "name": "installment"}],
        "specialists": {
          "medical": [],
          "unifiedMedical": [],
          "pediatric": []
        },
        "accessibility": [],
        "promotions": {
          "types": [],
          "cashbackPercent": "5%",
          "snippetPriceText": "16000 ₽",
          "snippetOfferText": "Professional cleaning",
          "hasGoodPlace": true,
          "hasVtbOffer": true,
          "hasFreeExamination": false,
          "hasInstallments": true
        }
      }
    }
  ],
  "count": 1,
  "total": 1,
  "limit": 100,
  "offset": 0
}
```

```http
GET /api/v2/organizations/{id}
```

Returns one enriched card by `id`, `yandexId` or `permalink`.

```http
GET /api/v2/meta
```

Returns source metadata, category counts and the v2 feature-list contract.

### Review AI Analysis

```http
GET /api/v2/organizations/{id}/reviews/ai-analysis
GET /api/v2/organizations/{id}/reviews/ai-radius-analysis?radius_m=3000
POST /api/v2/organizations/{id}/reviews/ai-analysis
POST /api/v2/organizations/{id}/reviews/ai-analysis?refresh=true
POST /api/v2/reviews/analyze
```

The recommended production flow is precomputed:

```text
reviews CSV -> scripts/precompute_review_ai_reports.py -> data/analytics/review_ai/{org_id}.json -> fast GET
ready organization reports -> scripts/precompute_review_ai_radius_reports.py -> data/analytics/review_ai_radius/{center_org_id}_{radius_m}.json -> fast GET
```

`GET /api/v2/organizations/{id}/reviews/ai-analysis` only reads a fresh local cache file. It does
not call LM Studio or any other AI provider. When a fresh report is missing, it returns HTTP 404
with `status: "missing"`.

`POST /api/v2/organizations/{id}/reviews/ai-analysis` remains a manual refresh/debug endpoint and
may call the configured AI provider. Use `POST ...?refresh=true` to ignore the current cache.

`GET /api/v2/organizations/{id}/reviews/ai-radius-analysis?radius_m=3000` reads only a fresh
precomputed radius cache. It does not call LM Studio. It compares ready individual organization
reports inside the selected radius and returns `analysisText`; when the radius report is missing,
it returns HTTP 404 with `status: "missing"`.

The batch script reads collected reviews from a semicolon-delimited CSV, anonymizes them, and sends
only `rating`, optional `date`, and `text` to the local LM Studio server. Author names, review IDs
and URLs are not sent.
`POST /api/v2/reviews/analyze` is a compatibility route for frontends that do not call the
organization-scoped URL yet; it accepts `orgId`, `organizationId`, `id`, `yandexId` or `permalink`
in JSON/query params and falls back to the only organization in the reviews CSV when there is exactly one.

Default review source:

```text
data/output/reviews.csv
```

Configuration:

- `YANDEX_SCRAPER_REVIEW_AI_PROVIDER`: `gemini`, `openrouter`, `ollama` or `lmstudio`; default `gemini`.
- `YANDEX_SCRAPER_REVIEW_AI_MODEL`: generic model override for the selected provider.
- `YANDEX_SCRAPER_REVIEW_AI_TIMEOUT_SEC`: default `30`.
- `YANDEX_SCRAPER_REVIEW_AI_MAX_REVIEWS`: default `500`.
- `OPENROUTER_API_KEY`: required for `openrouter`.
- `OLLAMA_BASE_URL`: default `http://127.0.0.1:11434`.
- `LMSTUDIO_BASE_URL`: default `http://127.0.0.1:1234/v1`.
- `YANDEX_SCRAPER_OPENROUTER_MODEL`: default `openrouter/free`.
- `YANDEX_SCRAPER_OLLAMA_MODEL`: default `qwen2.5:7b`.
- `YANDEX_SCRAPER_LMSTUDIO_MODEL`: default `local-model`.
- `GEMINI_API_KEY` or `GOOGLE_API_KEY`: required for `gemini`.
- `YANDEX_SCRAPER_GEMINI_MODEL`: default `gemini-3-flash-preview`.
- `YANDEX_SCRAPER_REVIEWS_ANALYTICS_SOURCE_FILE`: override the reviews CSV path.
- `YANDEX_SCRAPER_REVIEW_AI_CACHE_DIR`: override the JSON cache directory.
- `YANDEX_SCRAPER_REVIEW_AI_RADIUS_CACHE_DIR`: override the radius JSON cache directory.

Precompute one organization with LM Studio:

```powershell
$env:YANDEX_SCRAPER_REVIEW_AI_PROVIDER="lmstudio"
$env:YANDEX_SCRAPER_LMSTUDIO_MODEL="google/gemma-4-e2b"
$env:YANDEX_SCRAPER_REVIEWS_ANALYTICS_SOURCE_FILE="data/output/reviews.csv"
$env:YANDEX_SCRAPER_REVIEW_AI_TIMEOUT_SEC="180"
python scripts/precompute_review_ai_reports.py --org-id 1590459763 --refresh
```

Useful batch flags:

- `--limit`
- `--org-id`
- `--refresh`
- `--sleep-sec`
- `--max-reviews`
- `--model`

Precompute one radius report after individual organization reports are ready:

```powershell
$env:YANDEX_SCRAPER_REVIEW_AI_PROVIDER="lmstudio"
$env:YANDEX_SCRAPER_LMSTUDIO_MODEL="google/gemma-4-e2b"
$env:YANDEX_SCRAPER_REVIEW_AI_TIMEOUT_SEC="180"
python scripts/precompute_review_ai_radius_reports.py --center-org-id 1590459763 --radius-m 3000 --refresh --model google/gemma-4-e2b
```

Useful radius batch flags:

- `--center-org-id` / `--org-id`
- `--radius-m`
- `--limit`
- `--refresh`
- `--sleep-sec`
- `--max-reports`
- `--max-report-chars`
- `--model`

The compatibility route `POST /api/v2/reviews/analyze` returns `analysis` as a formatted string for
the current Flutter client and also includes the structured object as `analysisDetails`.

OpenRouter free-router test:

```powershell
$env:YANDEX_SCRAPER_REVIEW_AI_PROVIDER="openrouter"
$env:YANDEX_SCRAPER_REVIEW_AI_MODEL="openrouter/free"
$env:OPENROUTER_API_KEY="your-key"
python scripts/run_api.py
```

Ollama local test:

```powershell
$env:YANDEX_SCRAPER_REVIEW_AI_PROVIDER="ollama"
$env:YANDEX_SCRAPER_REVIEW_AI_MODEL="qwen2.5:7b"
$env:OLLAMA_BASE_URL="http://127.0.0.1:11434"
python scripts/run_api.py
```

LM Studio local test:

```powershell
$env:YANDEX_SCRAPER_REVIEW_AI_PROVIDER="lmstudio"
$env:YANDEX_SCRAPER_REVIEW_AI_MODEL="local-model"
$env:LMSTUDIO_BASE_URL="http://127.0.0.1:1234/v1"
$env:YANDEX_SCRAPER_REVIEW_AI_TIMEOUT_SEC="180"
python scripts/run_api.py
```

Gemini test:

```powershell
$env:YANDEX_SCRAPER_REVIEW_AI_PROVIDER="gemini"
$env:GEMINI_API_KEY="your-key"
python scripts/run_api.py
```

```http
POST /api/v2/organizations/1109354351/reviews/ai-analysis
```

Response shape:

```json
{
  "organizationId": "1109354351",
  "organizationTitle": "Альтернатива",
  "status": "ready",
  "cached": false,
  "generatedAt": "2026-04-28T10:00:00+00:00",
  "provider": {
    "name": "openrouter",
    "model": "openrouter/free"
  },
  "source": {
    "reviewsCount": 50,
    "usedReviewsCount": 50
  },
  "ratingStats": {
    "average": 3.8,
    "distribution": {
      "5": 32,
      "4": 2,
      "3": 3,
      "2": 0,
      "1": 13
    },
    "ratedCount": 50
  },
  "analysis": {
    "summary": "...",
    "strengths": [],
    "weaknesses": [],
    "themes": [],
    "risks": [],
    "recommendations": [],
    "limitations": []
  },
  "analysisText": "Готовый человекочитаемый отчет..."
}
```

## Flutter Example

Add an HTTP client package to the Flutter project:

```powershell
flutter pub add http
```

Dart model:

```dart
class Organization {
  final String id;
  final String title;
  final String address;
  final String categories;
  final String phone;
  final String rating;
  final int reviewCount;
  final double lat;
  final double lon;

  Organization({
    required this.id,
    required this.title,
    required this.address,
    required this.categories,
    required this.phone,
    required this.rating,
    required this.reviewCount,
    required this.lat,
    required this.lon,
  });

  factory Organization.fromJson(Map<String, dynamic> json) {
    return Organization(
      id: json['id'] as String,
      title: json['title'] as String? ?? '',
      address: json['fullAddress'] as String? ?? '',
      categories: json['categories'] as String? ?? '',
      phone: json['phones_0_number'] as String? ?? '',
      rating: json['ratingData_ratingValue'] as String? ?? '',
      reviewCount: json['ratingData_ratingCount'] as int? ?? 0,
      lat: (json['lat'] as num).toDouble(),
      lon: (json['lon'] as num).toDouble(),
    );
  }
}
```

Fetch data:

```dart
import 'dart:convert';
import 'package:http/http.dart' as http;

Future<List<Organization>> fetchOrganizations() async {
  final uri = Uri.parse('http://127.0.0.1:8000/api/organizations?limit=5000');
  final response = await http.get(uri);

  if (response.statusCode != 200) {
    throw Exception('API error: ${response.statusCode}');
  }

  final decoded = jsonDecode(response.body) as Map<String, dynamic>;
  final items = decoded['items'] as List<dynamic>;

  return items
      .map((item) => Organization.fromJson(item as Map<String, dynamic>))
      .toList();
}
```

For Android emulator, replace `127.0.0.1` with `10.0.2.2`.
For a real phone, run the API with `--host 0.0.0.0` and use the computer LAN IP.

## Deployment Notes

For a small deployment, run the API behind a reverse proxy and keep `data/output/organizations.db` updated after the parser/export pipeline refreshes `data/output/result.csv`.

Set allowed CORS origins when exposing the API publicly:

```powershell
$env:YANDEX_SCRAPER_API_CORS_ORIGINS="https://your-site.example"
python scripts/run_api.py --host 0.0.0.0 --port 8000
```
