import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/organization.dart';
import '../models/review_dynamics.dart';

class OrganizationsBounds {
  const OrganizationsBounds({
    required this.north,
    required this.south,
    required this.east,
    required this.west,
  });

  final double north;
  final double south;
  final double east;
  final double west;

  Map<String, String> toQueryParameters() {
    return <String, String>{
      'bbox': [
        west.toStringAsFixed(6),
        south.toStringAsFixed(6),
        east.toStringAsFixed(6),
        north.toStringAsFixed(6),
      ].join(','),
    };
  }

  String cacheKey({int precision = 3}) {
    return [
      north.toStringAsFixed(precision),
      south.toStringAsFixed(precision),
      east.toStringAsFixed(precision),
      west.toStringAsFixed(precision),
    ].join(':');
  }
}

class OrganizationsApiClient {
  OrganizationsApiClient({
    required String baseUrl,
    http.Client? client,
    this.timeout = const Duration(seconds: 20),
    this.analysisTimeout = const Duration(seconds: 20),
  }) : baseUri = Uri.parse(_normalizeBaseUrl(baseUrl)),
       client = client ?? http.Client();

  static const defaultLimit = 1000;
  static const organizationsPath = '/api/organizations';

  final Uri baseUri;
  final http.Client client;
  final Duration timeout;
  final Duration analysisTimeout;

  String get baseUrl => baseUri.toString();

  Future<List<Organization>> fetchOrganizations({
    int limit = defaultLimit,
    int offset = 0,
    String? query,
    String? category,
    OrganizationsBounds? bounds,
  }) async {
    final uri = baseUri.replace(
      path: _joinPath(baseUri.path, organizationsPath),
      queryParameters: <String, String>{
        'limit': limit.toString(),
        'offset': offset.toString(),
        if (query != null && query.trim().isNotEmpty) 'q': query.trim(),
        if (category != null && category.trim().isNotEmpty)
          'category': category.trim(),
        if (bounds != null) ...bounds.toQueryParameters(),
      },
    );

    final http.Response response;
    try {
      response = await client.get(uri).timeout(timeout);
    } on TimeoutException {
      throw const OrganizationsApiException('API не ответил за 20 секунд');
    } on Object catch (error) {
      throw OrganizationsApiException('Не удалось подключиться к API: $error');
    }

    if (response.statusCode != 200) {
      throw OrganizationsApiException('API вернул HTTP ${response.statusCode}');
    }

    final decoded = jsonDecode(response.body);
    if (decoded is List) {
      return decoded
          .whereType<Map>()
          .map((item) => Organization.fromJson(Map<String, dynamic>.from(item)))
          .toList(growable: false);
    }
    if (decoded is! Map<String, dynamic>) {
      throw const OrganizationsApiException('API вернул неожиданный JSON');
    }

    final items = decoded['items'];
    if (items is! List) {
      throw const OrganizationsApiException('В ответе API нет массива items');
    }

    return items
        .whereType<Map>()
        .map((item) => Organization.fromJson(Map<String, dynamic>.from(item)))
        .toList(growable: false);
  }

  Future<String> analyzeReviews(String organizationId) async {
    final normalizedOrganizationId = organizationId.trim();
    if (normalizedOrganizationId.isEmpty) {
      throw const OrganizationsApiException(
        'Organization id is required for reviews analysis',
      );
    }

    final uri = baseUri.replace(
      pathSegments: _joinPathSegments(
        baseUri.pathSegments,
        const ['api', 'v2', 'organizations'],
        [normalizedOrganizationId, 'reviews', 'ai-analysis'],
      ),
      queryParameters: const <String, String>{},
    );

    final http.Response response;
    try {
      response = await client
          .get(uri, headers: const {'Accept': 'application/json'})
          .timeout(analysisTimeout);
    } on TimeoutException {
      throw OrganizationsApiException(
        'API reviews analysis did not answer in ${analysisTimeout.inSeconds} seconds',
      );
    } on Object catch (error) {
      throw OrganizationsApiException(
        'Could not connect to reviews analysis API: $error',
      );
    }

    if (response.statusCode != 200) {
      if (response.statusCode == 404) {
        throw const OrganizationsApiException(
          'AI-отчет еще не сформирован. Запустите предварительную генерацию отчетов.',
        );
      }
      throw OrganizationsApiException(
        'Reviews analysis API returned HTTP ${response.statusCode}',
      );
    }

    final decoded = jsonDecode(response.body);
    if (decoded is! Map<String, dynamic>) {
      throw const OrganizationsApiException(
        'Reviews analysis API returned unexpected JSON',
      );
    }

    final analysisResponse = _analysisResponseFrom(decoded);
    _validateAnalysisResponse(analysisResponse, normalizedOrganizationId);

    final analysisText = _analysisTextFrom(analysisResponse);
    if (analysisText == null || analysisText.isEmpty) {
      throw const OrganizationsApiException(
        'Reviews analysis API response does not contain analysis',
      );
    }

    return analysisText;
  }

  Future<String> analyzeRadiusReviews(
    String organizationId,
    int radiusM,
  ) async {
    final normalizedOrganizationId = organizationId.trim();
    if (normalizedOrganizationId.isEmpty) {
      throw const OrganizationsApiException(
        'Organization id is required for radius reviews analysis',
      );
    }
    if (radiusM < 1) {
      throw const OrganizationsApiException(
        'Radius must be greater than 0 for radius reviews analysis',
      );
    }

    final uri = baseUri.replace(
      pathSegments: _joinPathSegments(
        baseUri.pathSegments,
        const ['api', 'v2', 'organizations'],
        [normalizedOrganizationId, 'reviews', 'ai-radius-analysis'],
      ),
      queryParameters: <String, String>{'radius_m': radiusM.toString()},
    );

    final http.Response response;
    try {
      response = await client
          .get(uri, headers: const {'Accept': 'application/json'})
          .timeout(analysisTimeout);
    } on TimeoutException {
      throw OrganizationsApiException(
        'Radius reviews analysis API did not answer in ${analysisTimeout.inSeconds} seconds',
      );
    } on Object catch (error) {
      throw OrganizationsApiException(
        'Could not connect to radius reviews analysis API: $error',
      );
    }

    if (response.statusCode != 200) {
      if (response.statusCode == 404) {
        throw const OrganizationsApiException(
          'AI-отчет по радиусу еще не сформирован. Запустите предварительную генерацию отчетов по радиусу.',
        );
      }
      throw OrganizationsApiException(
        'Radius reviews analysis API returned HTTP ${response.statusCode}',
      );
    }

    final decoded = jsonDecode(response.body);
    if (decoded is! Map<String, dynamic>) {
      throw const OrganizationsApiException(
        'Radius reviews analysis API returned unexpected JSON',
      );
    }

    final analysisResponse = _analysisResponseFrom(decoded);
    _validateRadiusAnalysisResponse(
      analysisResponse,
      normalizedOrganizationId,
      radiusM,
    );

    final analysisText = _analysisTextFrom(analysisResponse);
    if (analysisText == null || analysisText.isEmpty) {
      throw const OrganizationsApiException(
        'Radius reviews analysis API response does not contain analysis',
      );
    }

    return analysisText;
  }

  Future<ReviewDynamics?> fetchReviewDynamics(String organizationId) async {
    final normalizedOrganizationId = organizationId.trim();
    if (normalizedOrganizationId.isEmpty) {
      throw const OrganizationsApiException(
        'Organization id is required for review dynamics',
      );
    }

    final uri = baseUri.replace(
      pathSegments: _joinPathSegments(
        baseUri.pathSegments,
        const ['api', 'v2', 'organizations'],
        [normalizedOrganizationId, 'reviews', 'dynamics'],
      ),
      queryParameters: const <String, String>{},
    );

    final http.Response response;
    try {
      response = await client
          .get(uri, headers: const {'Accept': 'application/json'})
          .timeout(analysisTimeout);
    } on TimeoutException {
      throw OrganizationsApiException(
        'Review dynamics API did not answer in ${analysisTimeout.inSeconds} seconds',
      );
    } on Object catch (error) {
      throw OrganizationsApiException(
        'Could not connect to review dynamics API: $error',
      );
    }

    if (response.statusCode == 404 || response.statusCode == 204) {
      return null;
    }
    if (response.statusCode != 200) {
      throw OrganizationsApiException(
        'Review dynamics API returned HTTP ${response.statusCode}',
      );
    }

    final decoded = jsonDecode(response.body);
    return ReviewDynamics.fromResponse(decoded);
  }

  static Map<String, dynamic> _analysisResponseFrom(
    Map<String, dynamic> decoded,
  ) {
    for (final key in const ['data', 'result']) {
      final wrapped = _readAnalysisMap(decoded[key]);
      if (wrapped.isNotEmpty &&
          (wrapped.containsKey('analysis') ||
              wrapped.containsKey('organizationId') ||
              wrapped.containsKey('centerOrganizationId') ||
              wrapped.containsKey('source'))) {
        return <String, dynamic>{...decoded, ...wrapped};
      }
    }

    return decoded;
  }

  static void _validateAnalysisResponse(
    Map<String, dynamic> response,
    String expectedOrganizationId,
  ) {
    final organizationId = _readAnalysisString(
      response['organizationId'] ?? response['organization_id'],
    );
    if (organizationId.isEmpty) {
      throw const OrganizationsApiException(
        'Reviews analysis API response does not contain organizationId',
      );
    }
    if (organizationId != expectedOrganizationId) {
      throw OrganizationsApiException(
        'Reviews analysis API returned organizationId $organizationId '
        'instead of $expectedOrganizationId',
      );
    }

    final source = _readAnalysisMap(response['source']);
    if (source['reviewsCount'] is! num || source['usedReviewsCount'] is! num) {
      throw const OrganizationsApiException(
        'Reviews analysis API response does not contain source review counts',
      );
    }
  }

  static void _validateRadiusAnalysisResponse(
    Map<String, dynamic> response,
    String expectedOrganizationId,
    int expectedRadiusM,
  ) {
    final organizationId = _readAnalysisString(
      response['centerOrganizationId'] ??
          response['center_organization_id'] ??
          response['organizationId'] ??
          response['organization_id'],
    );
    if (organizationId.isEmpty) {
      throw const OrganizationsApiException(
        'Radius reviews analysis API response does not contain centerOrganizationId',
      );
    }
    if (organizationId != expectedOrganizationId) {
      throw OrganizationsApiException(
        'Radius reviews analysis API returned centerOrganizationId '
        '$organizationId instead of $expectedOrganizationId',
      );
    }

    final radius = response['radiusM'] ?? response['radius_m'];
    if (radius is num && radius.toInt() != expectedRadiusM) {
      throw OrganizationsApiException(
        'Radius reviews analysis API returned radius $radius '
        'instead of $expectedRadiusM',
      );
    }

    final source = _readAnalysisMap(response['source']);
    if (source['usedReportsCount'] is! num) {
      throw const OrganizationsApiException(
        'Radius reviews analysis API response does not contain source report counts',
      );
    }
  }

  static String? _analysisTextFrom(Map<String, dynamic> decoded) {
    final analysisText = _readAnalysisString(decoded['analysisText']);
    if (analysisText.isNotEmpty) {
      return analysisText;
    }

    final analysis = _analysisTextFromValue(decoded['analysis'], decoded);
    if (analysis != null && analysis.isNotEmpty) {
      return analysis;
    }

    for (final key in const ['data', 'result']) {
      final wrappedAnalysis = _analysisTextFromValue(decoded[key], decoded);
      if (wrappedAnalysis != null && wrappedAnalysis.isNotEmpty) {
        return wrappedAnalysis;
      }
    }

    return _formatStructuredAnalysis(decoded, decoded);
  }

  static String? _analysisTextFromValue(
    Object? value,
    Map<String, dynamic> response,
  ) {
    if (value is String) {
      final trimmed = value.trim();
      return trimmed.isEmpty ? null : trimmed;
    }
    if (value is Map) {
      final map = Map<String, dynamic>.from(value);
      final nestedAnalysis = _analysisTextFromValue(
        map['analysis'],
        <String, dynamic>{...response, ...map},
      );
      if (nestedAnalysis != null && nestedAnalysis.isNotEmpty) {
        return nestedAnalysis;
      }

      return _formatStructuredAnalysis(map, <String, dynamic>{
        ...response,
        ...map,
      });
    }

    return null;
  }

  static String? _formatStructuredAnalysis(
    Map<String, dynamic> analysis,
    Map<String, dynamic> response,
  ) {
    final lines = <String>[];
    final organizationTitle = _readAnalysisString(
      response['organizationTitle'] ?? response['centerOrganizationTitle'],
    );
    final generatedAt = _readAnalysisString(response['generatedAt']);
    final source = _readAnalysisMap(response['source']);
    final ratingStats = _readAnalysisMap(response['ratingStats']);

    if (organizationTitle.isNotEmpty) {
      lines.add('Организация: $organizationTitle');
    }
    if (generatedAt.isNotEmpty) {
      lines.add('Сформировано: $generatedAt');
    }

    final reviewsCount = source['reviewsCount'];
    final usedReviewsCount = source['usedReviewsCount'];
    if (reviewsCount != null || usedReviewsCount != null) {
      final used = usedReviewsCount ?? reviewsCount;
      final total = reviewsCount ?? usedReviewsCount;
      lines.add('Отзывы: использовано $used из $total');
    }

    final average = ratingStats['average'];
    if (average != null) {
      lines.add('Средняя оценка: $average');
    }

    _addAnalysisSection(lines, 'Сводка', analysis['summary']);
    _addAnalysisSection(lines, 'Сильные стороны', analysis['strengths']);
    _addAnalysisSection(lines, 'Проблемы', analysis['weaknesses']);
    _addAnalysisSection(lines, 'Темы', analysis['themes']);
    _addAnalysisSection(lines, 'Риски', analysis['risks']);
    _addAnalysisSection(lines, 'Рекомендации', analysis['recommendations']);
    _addAnalysisSection(lines, 'Ограничения', analysis['limitations']);

    final text = lines.join('\n\n').trim();
    return text.isEmpty ? null : text;
  }

  static void _addAnalysisSection(
    List<String> lines,
    String title,
    Object? value,
  ) {
    final items = _analysisItems(value);
    if (items.isEmpty) {
      return;
    }

    if (items.length == 1) {
      lines.add('$title:\n${items.single}');
      return;
    }

    lines.add('$title:\n${items.map((item) => '- $item').join('\n')}');
  }

  static List<String> _analysisItems(Object? value) {
    if (value == null) {
      return const [];
    }
    if (value is String) {
      final trimmed = value.trim();
      return trimmed.isEmpty ? const [] : [trimmed];
    }
    if (value is List) {
      return value
          .map(_formatAnalysisItem)
          .where((item) => item.isNotEmpty)
          .toList(growable: false);
    }
    final formatted = _formatAnalysisItem(value);
    return formatted.isEmpty ? const [] : [formatted];
  }

  static String _formatAnalysisItem(Object? value) {
    if (value == null) {
      return '';
    }
    if (value is String) {
      return value.trim();
    }
    if (value is num || value is bool) {
      return value.toString();
    }
    if (value is Map) {
      final map = Map<String, dynamic>.from(value);
      final title = _readAnalysisString(
        map['title'] ?? map['name'] ?? map['theme'] ?? map['label'],
      );
      final text = _readAnalysisString(
        map['text'] ??
            map['description'] ??
            map['summary'] ??
            map['recommendation'] ??
            map['value'],
      );
      if (title.isNotEmpty && text.isNotEmpty) {
        return '$title: $text';
      }
      if (title.isNotEmpty) {
        return title;
      }
      if (text.isNotEmpty) {
        return text;
      }
      return jsonEncode(map);
    }
    return value.toString().trim();
  }

  static Map<String, dynamic> _readAnalysisMap(Object? value) {
    if (value is Map<String, dynamic>) {
      return value;
    }
    if (value is Map) {
      return Map<String, dynamic>.from(value);
    }
    return const {};
  }

  static String _readAnalysisString(Object? value) {
    if (value == null || value is List || value is Map) {
      return '';
    }
    return value.toString().trim();
  }

  static String _normalizeBaseUrl(String value) {
    final trimmed = value.trim();
    if (trimmed.isEmpty) {
      return 'http://127.0.0.1:8000';
    }
    return trimmed.replaceFirst(RegExp(r'/+$'), '');
  }

  static String _joinPath(String basePath, String apiPath) {
    final normalizedBase = basePath.replaceFirst(RegExp(r'/+$'), '');
    if (normalizedBase.isEmpty) {
      return apiPath;
    }
    return '$normalizedBase$apiPath';
  }

  static List<String> _joinPathSegments(
    List<String> baseSegments,
    List<String> prefixSegments,
    List<String> suffixSegments,
  ) {
    return <String>[
      ...baseSegments.where((segment) => segment.isNotEmpty),
      ...prefixSegments,
      ...suffixSegments,
    ];
  }
}

class OrganizationsApiException implements Exception {
  const OrganizationsApiException(this.message);

  final String message;

  @override
  String toString() => message;
}
