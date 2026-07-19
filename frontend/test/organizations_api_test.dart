import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:ui/services/organizations_api.dart';

void main() {
  test(
    'fetchOrganizations uses legacy endpoint and reads items envelope',
    () async {
      final client = _FakeClient((request) async {
        return http.Response(
          '{"items":[{"id":"1","title":"Cafe","lat":59.1,"lon":30.2}]}',
          200,
        );
      });
      final apiClient = OrganizationsApiClient(
        baseUrl: 'http://127.0.0.1:8000',
        client: client,
      );

      final organizations = await apiClient.fetchOrganizations(limit: 10);

      expect(client.lastUri?.path, '/api/organizations');
      expect(client.lastUri?.queryParameters['limit'], '10');
      expect(organizations, hasLength(1));
      expect(organizations.single.id, '1');
    },
  );

  test('fetchOrganizations sends viewport bounds as API bbox', () async {
    final client = _FakeClient((request) async {
      return http.Response('{"items":[]}', 200);
    });
    final apiClient = OrganizationsApiClient(
      baseUrl: 'http://127.0.0.1:8000',
      client: client,
    );

    await apiClient.fetchOrganizations(
      bounds: const OrganizationsBounds(
        west: 29.45,
        south: 59.6,
        east: 30.75,
        north: 60.2,
      ),
    );

    expect(
      client.lastUri?.queryParameters['bbox'],
      '29.450000,59.600000,30.750000,60.200000',
    );
    expect(client.lastUri?.queryParameters.containsKey('north'), isFalse);
  });

  test('fetchOrganizations reads root array response', () async {
    final client = _FakeClient((request) async {
      return http.Response(
        '[{"id":"2","title":"Bakery","lat":59.3,"lon":30.4}]',
        200,
      );
    });
    final apiClient = OrganizationsApiClient(
      baseUrl: 'http://127.0.0.1:8000',
      client: client,
    );

    final organizations = await apiClient.fetchOrganizations(limit: 10);

    expect(organizations, hasLength(1));
    expect(organizations.single.id, '2');
  });

  test(
    'analyzeReviews gets precomputed analysisText by organization id',
    () async {
      final client = _FakeClient((request) async {
        return http.Response(
          '{"organizationId":"1590459763","source":{"reviewsCount":2,'
          '"usedReviewsCount":2},"analysisText":"Reviews summary"}',
          200,
        );
      });
      final apiClient = OrganizationsApiClient(
        baseUrl: 'http://127.0.0.1:8000',
        client: client,
      );

      final analysis = await apiClient.analyzeReviews('1590459763');

      expect(
        client.lastUri?.path,
        '/api/v2/organizations/1590459763/reviews/ai-analysis',
      );
      expect(client.lastUri?.queryParameters, isEmpty);
      expect(client.lastMethod, 'GET');
      expect(client.lastBody, isEmpty);
      expect(analysis, 'Reviews summary');
    },
  );

  test('analyzeReviews reports missing precomputed analysis', () async {
    final client = _FakeClient((request) async {
      return http.Response(
        '{"organizationId":"1590459763","status":"missing"}',
        404,
      );
    });
    final apiClient = OrganizationsApiClient(
      baseUrl: 'http://127.0.0.1:8000',
      client: client,
    );

    expect(
      apiClient.analyzeReviews('1590459763'),
      throwsA(
        isA<OrganizationsApiException>().having(
          (error) => error.message,
          'message',
          contains('AI-отчет еще не сформирован'),
        ),
      ),
    );
  });

  test('analyzeRadiusReviews gets precomputed radius analysisText', () async {
    final client = _FakeClient((request) async {
      return http.Response(
        '{"centerOrganizationId":"1590459763","radiusM":3000,'
        '"source":{"usedReportsCount":4},"analysisText":"Radius summary"}',
        200,
      );
    });
    final apiClient = OrganizationsApiClient(
      baseUrl: 'http://127.0.0.1:8000',
      client: client,
    );

    final analysis = await apiClient.analyzeRadiusReviews('1590459763', 3000);

    expect(
      client.lastUri?.path,
      '/api/v2/organizations/1590459763/reviews/ai-radius-analysis',
    );
    expect(client.lastUri?.queryParameters, {'radius_m': '3000'});
    expect(client.lastMethod, 'GET');
    expect(client.lastBody, isEmpty);
    expect(analysis, 'Radius summary');
  });

  test(
    'analyzeRadiusReviews reports missing precomputed radius analysis',
    () async {
      final client = _FakeClient((request) async {
        return http.Response(
          '{"centerOrganizationId":"1590459763","status":"missing"}',
          404,
        );
      });
      final apiClient = OrganizationsApiClient(
        baseUrl: 'http://127.0.0.1:8000',
        client: client,
      );

      expect(
        apiClient.analyzeRadiusReviews('1590459763', 3000),
        throwsA(
          isA<OrganizationsApiException>().having(
            (error) => error.message,
            'message',
            contains('радиусу'),
          ),
        ),
      );
    },
  );

  test('analyzeReviews uses dedicated cache analysis timeout', () async {
    final client = _FakeClient((request) async {
      await Future<void>.delayed(const Duration(milliseconds: 20));
      return http.Response(
        '{"organizationId":"1590459763","source":{"reviewsCount":2,'
        '"usedReviewsCount":2},"analysis":"Reviews summary"}',
        200,
      );
    });
    final apiClient = OrganizationsApiClient(
      baseUrl: 'http://127.0.0.1:8000',
      client: client,
      timeout: const Duration(milliseconds: 1),
      analysisTimeout: const Duration(seconds: 1),
    );

    final analysis = await apiClient.analyzeReviews('1590459763');

    expect(analysis, 'Reviews summary');
  });

  test('analyzeReviews rejects response for another organization', () async {
    final client = _FakeClient((request) async {
      return http.Response(
        '{"organizationId":"other","source":{"reviewsCount":2,'
        '"usedReviewsCount":2},"analysis":"Reviews summary"}',
        200,
      );
    });
    final apiClient = OrganizationsApiClient(
      baseUrl: 'http://127.0.0.1:8000',
      client: client,
    );

    expect(
      apiClient.analyzeReviews('1590459763'),
      throwsA(isA<OrganizationsApiException>()),
    );
  });

  test('analyzeReviews reads analysis from data envelope', () async {
    final client = _FakeClient((request) async {
      return http.Response(
        '{"data":{"organizationId":"1590459763","source":{"reviewsCount":2,'
        '"usedReviewsCount":2},"analysis":"Reviews summary"}}',
        200,
      );
    });
    final apiClient = OrganizationsApiClient(
      baseUrl: 'http://127.0.0.1:8000',
      client: client,
    );

    final analysis = await apiClient.analyzeReviews('1590459763');

    expect(analysis, 'Reviews summary');
  });

  test('analyzeReviews formats root structured analysis response', () async {
    final client = _FakeClient((request) async {
      return http.Response(
        '{"organizationId":"1590459763","source":{"reviewsCount":2,'
        '"usedReviewsCount":2},"summary":"Mostly positive",'
        '"strengths":["Service"]}',
        200,
      );
    });
    final apiClient = OrganizationsApiClient(
      baseUrl: 'http://127.0.0.1:8000',
      client: client,
    );

    final analysis = await apiClient.analyzeReviews('1590459763');

    expect(analysis, contains('Mostly positive'));
    expect(analysis, contains('Service'));
  });

  test('analyzeReviews formats structured analysis response', () async {
    final client = _FakeClient((request) async {
      return http.Response(
        '{"organizationId":"1590459763","organizationTitle":"Alternative",'
        '"source":{"reviewsCount":50,'
        '"usedReviewsCount":45},"ratingStats":{"average":4.2},'
        '"analysis":{"summary":"Mostly positive",'
        '"strengths":["Service"],"weaknesses":["Waiting time"],'
        '"recommendations":["Add staff"]}}',
        200,
      );
    });
    final apiClient = OrganizationsApiClient(
      baseUrl: 'http://127.0.0.1:8000',
      client: client,
    );

    final analysis = await apiClient.analyzeReviews('1590459763');

    expect(analysis, contains('Организация: Alternative'));
    expect(analysis, contains('Отзывы: использовано 45 из 50'));
    expect(analysis, contains('Средняя оценка: 4.2'));
    expect(analysis, contains('Сводка:\nMostly positive'));
    expect(analysis, contains('Сильные стороны:\nService'));
    expect(analysis, contains('Проблемы:\nWaiting time'));
    expect(analysis, contains('Рекомендации:\nAdd staff'));
  });

  test('fetchReviewDynamics reads review dynamics endpoint', () async {
    final client = _FakeClient((request) async {
      return http.Response(
        '{"data":{"organization_key":"1590459763",'
        '"organization_title":"Alternative",'
        '"reviews_last_7_days":2,"reviews_last_30_days":8,'
        '"reviews_last_90_days":20,"reviews_previous_30_days":5,'
        '"growth_30d_abs":3,"growth_30d_pct":"60,0",'
        '"avg_review_rating":4.4,"avg_rating_last_30_days":4.6,'
        '"negative_reviews_last_30_days":1,'
        '"negative_share_last_30_days":0.125,'
        '"dynamics_status":"active_growth"}}',
        200,
      );
    });
    final apiClient = OrganizationsApiClient(
      baseUrl: 'http://127.0.0.1:8000',
      client: client,
    );

    final dynamics = await apiClient.fetchReviewDynamics('1590459763');

    expect(
      client.lastUri?.path,
      '/api/v2/organizations/1590459763/reviews/dynamics',
    );
    expect(client.lastUri?.queryParameters, isEmpty);
    expect(client.lastMethod, 'GET');
    expect(dynamics?.organizationKey, '1590459763');
    expect(dynamics?.reviewsLast30Days, 8);
    expect(dynamics?.growth30dAbs, 3);
    expect(dynamics?.growth30dPct, 60);
    expect(dynamics?.currentRating, 4.6);
    expect(dynamics?.negativeReviewsLast30Days, 1);
    expect(dynamics?.dynamicsStatus, 'active_growth');
  });

  test('fetchReviewDynamics treats missing analytics as empty data', () async {
    final client = _FakeClient((request) async {
      return http.Response('{"status":"missing"}', 404);
    });
    final apiClient = OrganizationsApiClient(
      baseUrl: 'http://127.0.0.1:8000',
      client: client,
    );

    final dynamics = await apiClient.fetchReviewDynamics('1590459763');

    expect(dynamics, isNull);
  });
}

class _FakeClient extends http.BaseClient {
  _FakeClient(this.handler);

  final Future<http.Response> Function(http.BaseRequest request) handler;
  Uri? lastUri;
  String? lastMethod;
  List<int> lastBody = const [];

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    lastUri = request.url;
    lastMethod = request.method;
    if (request is http.Request) {
      lastBody = request.bodyBytes;
    }
    final response = await handler(request);

    return http.StreamedResponse(
      Stream<List<int>>.value(response.bodyBytes),
      response.statusCode,
      headers: response.headers,
      reasonPhrase: response.reasonPhrase,
      request: request,
    );
  }
}
