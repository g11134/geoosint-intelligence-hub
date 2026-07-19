import 'package:flutter/material.dart';

import 'screens/organizations_map_screen.dart';
import 'services/organizations_api.dart';

void main() {
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({
    super.key,
    this.organizationsLoader,
    this.reviewsAnalyzer,
    this.radiusReviewsAnalyzer,
    this.reviewDynamicsLoader,
    this.apiBaseUrl = const String.fromEnvironment(
      'API_BASE_URL',
      defaultValue: 'http://127.0.0.1:8000',
    ),
  });

  final OrganizationsLoader? organizationsLoader;
  final ReviewsAnalyzer? reviewsAnalyzer;
  final RadiusReviewsAnalyzer? radiusReviewsAnalyzer;
  final ReviewDynamicsLoader? reviewDynamicsLoader;
  final String apiBaseUrl;

  @override
  Widget build(BuildContext context) {
    final apiClient = OrganizationsApiClient(baseUrl: apiBaseUrl);
    final loader =
        organizationsLoader ??
        (request) => apiClient.fetchOrganizations(
          limit: request.limit,
          offset: request.offset,
          query: request.query,
          category: request.category,
          bounds: request.bounds,
        );
    final analyzer = reviewsAnalyzer ?? apiClient.analyzeReviews;
    final radiusAnalyzer =
        radiusReviewsAnalyzer ?? apiClient.analyzeRadiusReviews;
    final dynamicsLoader =
        reviewDynamicsLoader ?? apiClient.fetchReviewDynamics;

    return MaterialApp(
      title: 'Organizations Map',
      debugShowCheckedModeBanner: false,
      theme: _buildTheme(Brightness.light),
      darkTheme: _buildTheme(Brightness.dark),
      home: OrganizationsMapScreen(
        loadOrganizations: loader,
        analyzeReviews: analyzer,
        analyzeRadiusReviews: radiusAnalyzer,
        loadReviewDynamics: dynamicsLoader,
        apiBaseUrl: apiClient.baseUrl,
      ),
    );
  }
}

ThemeData _buildTheme(Brightness brightness) {
  final colorScheme = ColorScheme.fromSeed(
    seedColor: const Color(0xFF0EA5E9),
    brightness: brightness,
  );

  final baseTheme = ThemeData(
    colorScheme: colorScheme,
    useMaterial3: true,
    fontFamily: 'Segoe UI',
    fontFamilyFallback: const ['Inter', 'Roboto', 'Arial'],
  );

  return baseTheme.copyWith(
    appBarTheme: AppBarTheme(
      backgroundColor: colorScheme.surface,
      foregroundColor: colorScheme.onSurface,
      elevation: 0,
      centerTitle: false,
      titleTextStyle: baseTheme.textTheme.titleLarge?.copyWith(
        color: colorScheme.onSurface,
        fontWeight: FontWeight.w700,
      ),
    ),
    popupMenuTheme: PopupMenuThemeData(
      color: colorScheme.surface,
      elevation: 8,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
    ),
    listTileTheme: ListTileThemeData(
      iconColor: colorScheme.primary,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
    ),
  );
}
