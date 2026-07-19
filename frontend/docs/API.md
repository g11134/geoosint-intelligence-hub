# Organizations API

This API is a read-only layer over the current parser export:

- source file: `data/output/result.csv`
- source longitude column: `coordinates_0`
- source latitude column: `coordinates_1`

The parser pipeline, queue, state files, raw JSONL and Excel exports are not changed by the API.

## Install

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

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

Returns whether the CSV export exists and how many valid coordinate records are available.

### Metadata

```http
GET /api/meta
```

Returns source file metadata, total record count and category counts.

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
      "shortTitle": "Example",
      "fullAddress": "Saint Petersburg",
      "category": "Coffee shop",
      "phone": "+7...",
      "lat": 59.788392,
      "lon": 30.14371,
      "coordinates": {
        "lat": 59.788392,
        "lon": 30.14371
      },
      "permalink": "126736325393",
      "rating": {
        "count": 673,
        "countRaw": "673",
        "value": 4.7,
        "valueRaw": "4.699999809265137"
      },
      "source": {
        "query": "coffee",
        "bbox": "30.123910,59.785072~30.150726,59.798524"
      },
      "raw": {}
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
  final String category;
  final double lat;
  final double lon;

  Organization({
    required this.id,
    required this.title,
    required this.address,
    required this.category,
    required this.lat,
    required this.lon,
  });

  factory Organization.fromJson(Map<String, dynamic> json) {
    return Organization(
      id: json['id'] as String,
      title: json['title'] as String? ?? '',
      address: json['fullAddress'] as String? ?? '',
      category: json['category'] as String? ?? '',
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

For a small deployment, run the API behind a reverse proxy and keep `data/output/result.csv` updated by the parser/export pipeline.

Set allowed CORS origins when exposing the API publicly:

```powershell
$env:YANDEX_SCRAPER_API_CORS_ORIGINS="https://your-site.example"
python scripts/run_api.py --host 0.0.0.0 --port 8000
```
