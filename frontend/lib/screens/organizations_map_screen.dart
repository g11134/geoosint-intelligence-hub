import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';
import 'package:url_launcher/url_launcher.dart';

import '../models/organization.dart';
import '../models/review_dynamics.dart';
import '../services/organizations_api.dart';

typedef OrganizationsLoader =
    Future<List<Organization>> Function(OrganizationsLoadRequest request);
typedef ReviewsAnalyzer = Future<String> Function(String organizationId);
typedef RadiusReviewsAnalyzer =
    Future<String> Function(String organizationId, int radiusM);
typedef ReviewDynamicsLoader =
    Future<ReviewDynamics?> Function(String organizationId);

const _allCategoriesValue = '__all_categories__';
const _allChainsValue = '__all_chains__';
const _selectedSky = Color(0xFF7DD3FC);
const _selectedSkyBorder = Color(0xFF0284C7);
const _fadedSky = Color(0xFFEAF8FF);
const _fadedSkyBorder = Color(0xFFBAE6FD);
const _deepSkyText = Color(0xFF075985);
const _selectedRadiusMarker = Color(0xFFDDFBE8);
const _selectedRadiusMarkerBorder = Color(0xFF22C55E);
const _selectedRadiusMarkerText = Color(0xFF14532D);
const _chainMinCount = 5;
const _maxMapMarkers = 180;
const _maxDetailedMapMarkers = 14;
const _minMapLabelWidth = 108.0;
const _maxMapLabelWidth = 520.0;
const _compactMapLabelChromeWidth = 20.0;
const _detailedMapLabelChromeWidth = 22.0;
const _mapMarkerCollisionGap = 8.0;
const _maxSelectedNeighborPins = 20;
const _radiusPresetOptions = <int>[1000, 2000, 3000];
const _minRadiusM = 1000;
const _maxRadiusM = 3000;
const _radiusStepM = 100;
const _defaultRadiusM = 3000;
const _radiusStrokeColor = Color(0xFF2F80ED);
const _radiusFillColor = Color(0x332F80ED);
const _radiusFitPadding = EdgeInsets.all(40);
const _radiusDistance = DistanceHaversine(roundResult: false);
const _selectedOrganizationMapZoom = 14.0;
const _defaultMinMapZoom = 3.0;
const _containedMapZoomPadding = 0.05;
const _viewportOrganizationLimit = 250;
const _viewportLoadDebounceDuration = Duration(milliseconds: 350);
const _viewportCachePrecision = 3;
const _viewportRenderPrecision = 4;
const _hoverUpdateDebounceDuration = Duration(milliseconds: 60);
const _mapMarkerMeasurementCacheLimit = 4096;
const _mapTileUrl = String.fromEnvironment(
  'TILE_URL_TEMPLATE',
  defaultValue:
      'https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
);
const _mapTileFallbackUrlTemplate = String.fromEnvironment(
  'TILE_FALLBACK_URL_TEMPLATE',
  defaultValue: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
);
const _mapTileAttribution = String.fromEnvironment(
  'TILE_ATTRIBUTION',
  defaultValue: 'CARTO / OpenStreetMap contributors',
);
const _mapTileSubdomainsCsv = String.fromEnvironment('TILE_SUBDOMAINS');
final _mapMarkerTitleWidthCache = <String, double>{};
final _mapMarkerKpiBadgeWidthCache = <String, double>{};
final _mapBounds = LatLngBounds.unsafe(
  west: 29.45,
  south: 59.60,
  east: 30.75,
  north: 60.20,
);
final _initialLoadBounds = OrganizationsBounds(
  north: _mapBounds.north,
  south: _mapBounds.south,
  east: _mapBounds.east,
  west: _mapBounds.west,
);
const _mapMarkersEnabled = bool.fromEnvironment(
  'MAP_MARKERS_ENABLED',
  defaultValue: true,
);
const _mapPanelEnabled = bool.fromEnvironment(
  'MAP_PANEL_ENABLED',
  defaultValue: true,
);

List<String> _mapTileSubdomains() {
  if (_mapTileSubdomainsCsv.trim().isEmpty) {
    return const [];
  }

  return _mapTileSubdomainsCsv
      .split(',')
      .map((subdomain) => subdomain.trim())
      .where((subdomain) => subdomain.isNotEmpty)
      .toList(growable: false);
}

String? _mapTileFallbackUrl() {
  final fallbackUrl = _mapTileFallbackUrlTemplate.trim();
  return fallbackUrl.isEmpty ? null : fallbackUrl;
}

LatLng? _validPositionFor(Organization organization) {
  final lat = organization.lat;
  final lon = organization.lon;
  if (!lat.isFinite ||
      !lon.isFinite ||
      lat < -90 ||
      lat > 90 ||
      lon < -180 ||
      lon > 180) {
    return null;
  }
  return LatLng(lat, lon);
}

int _snapRadiusM(int radiusM) {
  final clamped = radiusM.clamp(_minRadiusM, _maxRadiusM).toInt();
  final snapped = (clamped / _radiusStepM).round() * _radiusStepM;
  return snapped.clamp(_minRadiusM, _maxRadiusM).toInt();
}

String _radiusLabelFor(int radiusM) {
  if (radiusM % 1000 == 0) {
    return '${radiusM ~/ 1000} км';
  }
  return '${(radiusM / 1000).toStringAsFixed(1)} км';
}

String _distanceLabelFor(double distanceM) {
  if (!distanceM.isFinite) {
    return '';
  }
  if (distanceM < 1000) {
    return '${distanceM.round()} м';
  }

  final distanceKm = distanceM / 1000;
  if (distanceKm >= 10 || distanceKm == distanceKm.roundToDouble()) {
    return '${distanceKm.round()} км';
  }
  return '${distanceKm.toStringAsFixed(1)} км';
}

String _qualityRatingText(double value) {
  return value.toStringAsFixed(1);
}

Map<String, int> _nearestRanksByIdFor(
  List<Organization> organizations,
  Organization? selected,
) {
  final selectedPosition = selected == null
      ? null
      : _validPositionFor(selected);
  if (selected == null || selectedPosition == null) {
    return const <String, int>{};
  }

  final distances = <MapEntry<String, double>>[];
  for (final organization in organizations) {
    if (organization.id == selected.id) {
      continue;
    }

    final position = _validPositionFor(organization);
    if (position == null) {
      continue;
    }

    final distance = _radiusDistance(selectedPosition, position);
    if (distance.isFinite) {
      distances.add(MapEntry(organization.id, distance));
    }
  }

  distances.sort((left, right) => left.value.compareTo(right.value));

  final ranks = <String, int>{};
  for (var index = 0; index < distances.length; index += 1) {
    ranks[distances[index].key] = index + 1;
  }
  return ranks;
}

LatLngBounds? _radiusBoundsFor(LatLng center, int radiusM) {
  final radius = radiusM.toDouble();
  if (!center.latitude.isFinite ||
      !center.longitude.isFinite ||
      !radius.isFinite ||
      radius <= 0) {
    return null;
  }

  try {
    return LatLngBounds.fromPoints([
      _radiusDistance.offset(center, radius, 0),
      _radiusDistance.offset(center, radius, 90),
      _radiusDistance.offset(center, radius, 180),
      _radiusDistance.offset(center, radius, 270),
    ]);
  } on Object {
    return null;
  }
}

const _chainStopWords = <String>{
  'ао',
  'бар',
  'в',
  'гипермаркет',
  'дома',
  'зао',
  'и',
  'ип',
  'кафе',
  'компания',
  'магазин',
  'маркет',
  'на',
  'оао',
  'общество',
  'ооо',
  'отделение',
  'пао',
  'представительство',
  'ресторан',
  'салон',
  'сеть',
  'супермаркет',
  'у',
  'филиал',
  'центр',
  'bar',
  'branch',
  'cafe',
  'company',
  'inc',
  'llc',
  'ltd',
  'market',
  'restaurant',
  'shop',
  'store',
  'the',
};

IconData _categoryIcon(String category) {
  final value = category.toLowerCase();

  if (value.contains('коф') || value.contains('coffee')) {
    return Icons.local_cafe_rounded;
  }
  if (value.contains('кафе') ||
      value.contains('ресторан') ||
      value.contains('еда') ||
      value.contains('бар') ||
      value.contains('пицц') ||
      value.contains('суши') ||
      value.contains('food') ||
      value.contains('restaurant')) {
    return Icons.restaurant_rounded;
  }
  if (value.contains('магаз') ||
      value.contains('market') ||
      value.contains('shop') ||
      value.contains('торгов')) {
    return Icons.shopping_bag_rounded;
  }
  if (value.contains('аптек') ||
      value.contains('мед') ||
      value.contains('clinic') ||
      value.contains('health')) {
    return Icons.local_hospital_rounded;
  }
  if (value.contains('банк') ||
      value.contains('финанс') ||
      value.contains('bank')) {
    return Icons.credit_card_rounded;
  }
  if (value.contains('отель') ||
      value.contains('гостин') ||
      value.contains('hotel')) {
    return Icons.hotel_rounded;
  }
  if (value.contains('салон') ||
      value.contains('красот') ||
      value.contains('beauty')) {
    return Icons.spa_rounded;
  }
  if (value.contains('спорт') || value.contains('fitness')) {
    return Icons.fitness_center_rounded;
  }
  if (value.contains('школ') ||
      value.contains('образ') ||
      value.contains('school')) {
    return Icons.school_rounded;
  }
  if (value.contains('авто') || value.contains('car')) {
    return Icons.directions_car_rounded;
  }

  return Icons.category_rounded;
}

IconData _organizationIcon(Organization organization) {
  return _categoryIcon(organization.categoryLabel);
}

int _clusterZoomBucket(double zoom) {
  if (zoom >= 17) {
    return 7;
  }
  if (zoom >= 14) {
    return 6;
  }
  if (zoom >= 13) {
    return 5;
  }
  if (zoom >= 12) {
    return 4;
  }
  if (zoom >= 11) {
    return 3;
  }
  if (zoom >= 10) {
    return 2;
  }
  if (zoom >= 9) {
    return 1;
  }
  return 0;
}

enum _OrganizationsSortMode {
  source('Порядок API', Icons.place_rounded),
  title('Название', Icons.sort_by_alpha_rounded),
  category('Категория', Icons.category_rounded),
  rating('Рейтинг', Icons.star_rounded),
  reviews('Отзывы', Icons.chat_bubble_rounded);

  const _OrganizationsSortMode(this.label, this.icon);

  final String label;
  final IconData icon;
}

enum _QualityFilterActionKind { reset, minRating, minReviews }

class _QualityFilterAction {
  const _QualityFilterAction.reset()
    : kind = _QualityFilterActionKind.reset,
      minRating = null,
      minReviews = null;

  const _QualityFilterAction.minRating(this.minRating)
    : kind = _QualityFilterActionKind.minRating,
      minReviews = null;

  const _QualityFilterAction.minReviews(this.minReviews)
    : kind = _QualityFilterActionKind.minReviews,
      minRating = null;

  final _QualityFilterActionKind kind;
  final double? minRating;
  final int? minReviews;
}

class _CategoryOption {
  const _CategoryOption({required this.name, required this.count});

  final String name;
  final int count;
}

class _ChainOption {
  const _ChainOption({
    required this.key,
    required this.name,
    required this.count,
  });

  final String key;
  final String name;
  final int count;
}

class _MapCluster {
  const _MapCluster({
    required this.organizations,
    required this.center,
    required this.representative,
    required this.key,
  });

  final List<Organization> organizations;
  final LatLng center;
  final Organization representative;
  final String key;

  int get count => organizations.length;
  bool get isSingle => count == 1;
}

enum _MapMarkerMode { cluster, pinOnly, neighborPin, compact, detailed }

class _MapMarkerItem {
  const _MapMarkerItem({
    required this.cluster,
    required this.mode,
    required this.selected,
    required this.hovered,
    required this.rank,
  });

  final _MapCluster cluster;
  final _MapMarkerMode mode;
  final bool selected;
  final bool hovered;
  final int? rank;
}

class OrganizationsLoadRequest {
  const OrganizationsLoadRequest({
    this.bounds,
    this.limit = _viewportOrganizationLimit,
    this.offset = 0,
    this.query,
    this.category,
  });

  final OrganizationsBounds? bounds;
  final int limit;
  final int offset;
  final String? query;
  final String? category;
}

class _OrganizationsViewData {
  const _OrganizationsViewData({
    required this.categories,
    required this.chains,
    required this.visibleOrganizations,
    required this.listedOrganizations,
    required this.mapOrganizations,
    required this.selectedOrganization,
  });

  final List<_CategoryOption> categories;
  final List<_ChainOption> chains;
  final List<Organization> visibleOrganizations;
  final List<Organization> listedOrganizations;
  final List<Organization> mapOrganizations;
  final Organization? selectedOrganization;
}

class OrganizationsMapScreen extends StatefulWidget {
  const OrganizationsMapScreen({
    super.key,
    required this.loadOrganizations,
    required this.analyzeReviews,
    required this.analyzeRadiusReviews,
    required this.loadReviewDynamics,
    required this.apiBaseUrl,
  });

  final OrganizationsLoader loadOrganizations;
  final ReviewsAnalyzer analyzeReviews;
  final RadiusReviewsAnalyzer analyzeRadiusReviews;
  final ReviewDynamicsLoader loadReviewDynamics;
  final String apiBaseUrl;

  @override
  State<OrganizationsMapScreen> createState() => _OrganizationsMapScreenState();
}

class _OrganizationsMapScreenState extends State<OrganizationsMapScreen>
    with SingleTickerProviderStateMixin {
  final MapController _mapController = MapController();
  late final AnimationController _mapAnimationController;

  final Map<String, Organization> _organizationsById = {};
  final Set<String> _loadedViewportKeys = {};
  final Set<String> _loadingViewportKeys = {};

  Timer? _viewportLoadDebounce;
  Timer? _hoverUpdateDebounce;
  String? _pendingHoveredOrganizationId;
  String? _lastRenderedViewportKey;
  List<Organization> _organizations = const [];
  bool _organizationsLoading = false;
  Object? _organizationsError;
  Organization? _selectedOrganization;
  bool _selectedDetailsExpanded = false;
  _OrganizationsSortMode _sortMode = _OrganizationsSortMode.source;
  String? _selectedCategory;
  String? _selectedChainKey;
  double? _minRatingFilter;
  int? _minReviewsFilter;
  double? _currentMapZoom;
  int _radiusM = _defaultRadiusM;
  bool _mapRadiusOnly = false;
  String? _hoveredOrganizationId;
  bool _reviewsAnalysisLoading = false;
  bool _radiusReviewsAnalysisLoading = false;
  final Map<String, ReviewDynamics?> _reviewDynamicsByOrganizationId = {};
  final Set<String> _reviewDynamicsLoadingIds = {};
  final Map<String, Object> _reviewDynamicsErrorsByOrganizationId = {};

  List<Organization>? _cachedCategorySource;
  List<_CategoryOption> _cachedCategories = const [];

  List<Organization>? _cachedChainSource;
  List<_ChainOption> _cachedChains = const [];

  List<Organization>? _cachedVisibleSource;
  String? _cachedVisibleCategory;
  String? _cachedVisibleChainKey;
  double? _cachedVisibleMinRating;
  int? _cachedVisibleMinReviews;
  _OrganizationsSortMode? _cachedVisibleSortMode;
  List<Organization> _cachedVisibleOrganizations = const [];

  List<Organization>? _cachedSelectedSource;
  String? _cachedSelectedId;
  Organization? _cachedSelectedOrganization;

  List<Organization>? _cachedMapSource;
  String? _cachedMapSelectedId;
  List<Organization> _cachedMapOrganizations = const [];

  List<Organization>? _cachedDerivedSource;
  final Map<String, List<String>> _cachedCategoriesByOrganizationId = {};
  final Map<String, String> _cachedChainKeysByOrganizationId = {};

  @override
  void initState() {
    super.initState();
    _mapAnimationController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 280),
    );
    _organizationsLoading = true;
    _loadOrganizationsForBounds(_initialLoadBounds, force: true);
  }

  @override
  void dispose() {
    _viewportLoadDebounce?.cancel();
    _hoverUpdateDebounce?.cancel();
    _mapAnimationController.dispose();
    _mapController.dispose();
    super.dispose();
  }

  void _reload() {
    _viewportLoadDebounce?.cancel();
    _cancelPendingHoverUpdate();
    final bounds = _currentLoadBounds() ?? _initialLoadBounds;
    setState(() {
      _selectedOrganization = null;
      _selectedDetailsExpanded = false;
      _currentMapZoom = null;
      _hoveredOrganizationId = null;
      _lastRenderedViewportKey = null;
      _organizationsError = null;
      _organizations = const [];
      _organizationsById.clear();
      _loadedViewportKeys.clear();
      _loadingViewportKeys.clear();
      _reviewDynamicsByOrganizationId.clear();
      _reviewDynamicsLoadingIds.clear();
      _reviewDynamicsErrorsByOrganizationId.clear();
      _resetViewCaches();
    });
    _loadOrganizationsForBounds(bounds, force: true);
  }

  Future<void> _loadOrganizationsForBounds(
    OrganizationsBounds bounds, {
    bool force = false,
  }) async {
    final cacheKey = bounds.cacheKey(precision: _viewportCachePrecision);
    if (!force &&
        (_loadedViewportKeys.contains(cacheKey) ||
            _loadingViewportKeys.contains(cacheKey))) {
      return;
    }

    _loadingViewportKeys.add(cacheKey);
    if (mounted) {
      setState(() {
        _organizationsLoading = true;
        _organizationsError = null;
      });
    }

    try {
      final loadedOrganizations = await widget.loadOrganizations(
        OrganizationsLoadRequest(
          bounds: bounds,
          limit: _viewportOrganizationLimit,
        ),
      );
      if (!mounted) {
        return;
      }

      setState(() {
        _mergeOrganizations(loadedOrganizations);
        _loadedViewportKeys.add(cacheKey);
        _organizationsError = null;
      });
    } on Object catch (error) {
      if (!mounted) {
        return;
      }

      setState(() => _organizationsError = error);
    } finally {
      _loadingViewportKeys.remove(cacheKey);
      if (mounted) {
        setState(() {
          _organizationsLoading = _loadingViewportKeys.isNotEmpty;
        });
      }
    }
  }

  void _mergeOrganizations(List<Organization> organizations) {
    if (organizations.isEmpty) {
      return;
    }

    for (final organization in organizations) {
      _organizationsById[organization.id] = organization;
    }
    _organizations = List<Organization>.unmodifiable(_organizationsById.values);
    _resetViewCaches();
  }

  void _resetViewCaches() {
    _cachedCategorySource = null;
    _cachedCategories = const [];
    _cachedChainSource = null;
    _cachedChains = const [];
    _cachedVisibleSource = null;
    _cachedVisibleCategory = null;
    _cachedVisibleChainKey = null;
    _cachedVisibleMinRating = null;
    _cachedVisibleMinReviews = null;
    _cachedVisibleSortMode = null;
    _cachedVisibleOrganizations = const [];
    _cachedSelectedSource = null;
    _cachedSelectedId = null;
    _cachedSelectedOrganization = null;
    _cachedMapSource = null;
    _cachedMapSelectedId = null;
    _cachedMapOrganizations = const [];
    _cachedDerivedSource = null;
    _cachedCategoriesByOrganizationId.clear();
    _cachedChainKeysByOrganizationId.clear();
  }

  void _selectOrganization(Organization organization) {
    _cancelPendingHoverUpdate();
    setState(() {
      _selectedOrganization = organization;
      _selectedDetailsExpanded = false;
      _hoveredOrganizationId = null;
    });
    _loadReviewDynamicsFor(organization.id);
    _fitRadiusFor(organization, _radiusM);
  }

  void _clearSelectedOrganization() {
    _cancelPendingHoverUpdate();
    setState(() {
      _selectedOrganization = null;
      _selectedDetailsExpanded = false;
      _hoveredOrganizationId = null;
    });
  }

  Future<void> _loadReviewDynamicsFor(
    String organizationId, {
    bool force = false,
  }) async {
    final normalizedOrganizationId = organizationId.trim();
    if (normalizedOrganizationId.isEmpty) {
      return;
    }
    if (!force &&
        (_reviewDynamicsByOrganizationId.containsKey(
              normalizedOrganizationId,
            ) ||
            _reviewDynamicsLoadingIds.contains(normalizedOrganizationId))) {
      return;
    }

    setState(() {
      _reviewDynamicsLoadingIds.add(normalizedOrganizationId);
      _reviewDynamicsErrorsByOrganizationId.remove(normalizedOrganizationId);
    });

    try {
      final dynamics = await widget.loadReviewDynamics(
        normalizedOrganizationId,
      );
      if (!mounted) {
        return;
      }
      setState(() {
        _reviewDynamicsByOrganizationId[normalizedOrganizationId] = dynamics;
        _reviewDynamicsErrorsByOrganizationId.remove(normalizedOrganizationId);
      });
    } on Object catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _reviewDynamicsErrorsByOrganizationId[normalizedOrganizationId] = error;
      });
    } finally {
      if (mounted) {
        setState(() {
          _reviewDynamicsLoadingIds.remove(normalizedOrganizationId);
        });
      } else {
        _reviewDynamicsLoadingIds.remove(normalizedOrganizationId);
      }
    }
  }

  ReviewDynamics? _reviewDynamicsFor(Organization? organization) {
    if (organization == null) {
      return null;
    }
    return _reviewDynamicsByOrganizationId[organization.id];
  }

  bool _reviewDynamicsLoadingFor(Organization? organization) {
    if (organization == null) {
      return false;
    }
    return _reviewDynamicsLoadingIds.contains(organization.id);
  }

  Object? _reviewDynamicsErrorFor(Organization? organization) {
    if (organization == null) {
      return null;
    }
    return _reviewDynamicsErrorsByOrganizationId[organization.id];
  }

  void _setMapRadiusOnly(bool mapRadiusOnly) {
    if (_mapRadiusOnly == mapRadiusOnly) {
      return;
    }

    setState(() => _mapRadiusOnly = mapRadiusOnly);
  }

  void _setHoveredOrganizationId(String? organizationId) {
    if (_hoveredOrganizationId == organizationId &&
        _pendingHoveredOrganizationId == organizationId) {
      return;
    }

    _pendingHoveredOrganizationId = organizationId;
    _hoverUpdateDebounce?.cancel();
    _hoverUpdateDebounce = Timer(_hoverUpdateDebounceDuration, () {
      _hoverUpdateDebounce = null;
      final nextHoveredOrganizationId = _pendingHoveredOrganizationId;
      _pendingHoveredOrganizationId = null;

      if (!mounted || _hoveredOrganizationId == nextHoveredOrganizationId) {
        return;
      }

      setState(() => _hoveredOrganizationId = nextHoveredOrganizationId);
    });
  }

  void _cancelPendingHoverUpdate() {
    _hoverUpdateDebounce?.cancel();
    _hoverUpdateDebounce = null;
    _pendingHoveredOrganizationId = null;
  }

  void _focusSelectedOrganization() {
    final selected = _selectedOrganization;
    if (selected == null) {
      return;
    }

    _fitRadiusFor(selected, _radiusM);
  }

  void _handleMapTap(LatLng point) {
    final selected = _selectedOrganization;
    if (selected == null) {
      return;
    }

    final selectedPosition = _validPositionFor(selected);
    if (selectedPosition == null) {
      return;
    }

    if (_radiusDistance(selectedPosition, point) <= _radiusM) {
      return;
    }

    _cancelPendingHoverUpdate();
    setState(() {
      _selectedOrganization = null;
      _selectedDetailsExpanded = false;
      _hoveredOrganizationId = null;
    });
  }

  void _setRadiusM(int radiusM) {
    final nextRadiusM = _snapRadiusM(radiusM);
    if (_radiusM == nextRadiusM) {
      return;
    }

    setState(() => _radiusM = nextRadiusM);
  }

  void _fitRadiusFor(Organization organization, int radiusM) {
    final position = _validPositionFor(organization);
    if (position == null) {
      return;
    }

    _mapAnimationController.stop();

    try {
      final radiusBounds = _radiusBoundsFor(position, radiusM);
      final fitBounds = radiusBounds == null
          ? null
          : _radiusBoundsWithVisibleOrganizations(radiusBounds);
      final moved = radiusBounds == null
          ? _mapController.move(position, _selectedOrganizationMapZoom)
          : _mapController.fitCamera(
              CameraFit.bounds(
                bounds: fitBounds!,
                padding: _radiusFitPadding,
                maxZoom: _selectedOrganizationMapZoom,
              ),
            );

      if (!moved && radiusBounds != null) {
        _mapController.move(position, _selectedOrganizationMapZoom);
      }
      _syncCurrentMapZoomFromController();
    } on Object {
      try {
        _mapController.move(position, _selectedOrganizationMapZoom);
        _syncCurrentMapZoomFromController();
      } on Object {
        // MapController can be unavailable during early widget lifecycle.
      }
    }
  }

  LatLngBounds _radiusBoundsWithVisibleOrganizations(
    LatLngBounds radiusBounds,
  ) {
    final points = <LatLng>[
      radiusBounds.southWest,
      radiusBounds.northEast,
      radiusBounds.northWest,
      radiusBounds.southEast,
    ];

    for (final organization in _visibleOrganizationsFor(_organizations)) {
      final position = _validPositionFor(organization);
      if (position != null) {
        points.add(position);
      }
    }

    return LatLngBounds.fromPoints(points);
  }

  void _syncCurrentMapZoomFromController() {
    if (!mounted) {
      return;
    }

    try {
      final zoom = _mapController.camera.zoom;
      if (_currentMapZoom == zoom) {
        return;
      }
      setState(() => _currentMapZoom = zoom);
    } on Object {
      return;
    }
  }

  void _openCluster(_MapCluster cluster) {
    final currentZoom = _currentMapZoom ?? _mapController.camera.zoom;
    final nextZoom = math.min(17.0, math.max(currentZoom + 2, 13.0));
    setState(() => _currentMapZoom = nextZoom);
    _animateMapMove(cluster.center, nextZoom);
  }

  void _changeMapZoomBy(double delta) {
    try {
      final camera = _mapController.camera;
      final minZoom = camera.minZoom ?? _defaultMinMapZoom;
      final maxZoom = camera.maxZoom ?? 17.0;
      final nextZoom = (camera.zoom + delta).clamp(minZoom, maxZoom).toDouble();
      if (nextZoom == camera.zoom) {
        return;
      }

      setState(() => _currentMapZoom = nextZoom);
      _animateMapMove(camera.center, nextZoom);
    } on Object {
      return;
    }
  }

  void _animateMapMove(LatLng center, double zoom) {
    final camera = _mapController.camera;
    final startCenter = camera.center;
    final startZoom = camera.zoom;
    final latTween = Tween<double>(
      begin: startCenter.latitude,
      end: center.latitude,
    );
    final lonTween = Tween<double>(
      begin: startCenter.longitude,
      end: center.longitude,
    );
    final zoomTween = Tween<double>(begin: startZoom, end: zoom);

    _mapAnimationController.stop();
    late final VoidCallback listener;
    listener = () {
      final curved = Curves.easeOutCubic.transform(
        _mapAnimationController.value,
      );
      _mapController.move(
        LatLng(latTween.transform(curved), lonTween.transform(curved)),
        zoomTween.transform(curved),
      );
    };
    _mapAnimationController
      ..reset()
      ..addListener(listener);
    _mapAnimationController.forward().whenCompleteOrCancel(() {
      _mapAnimationController.removeListener(listener);
      _syncCurrentMapZoomFromController();
    });
  }

  void _toggleSelectedDetails() {
    setState(() => _selectedDetailsExpanded = !_selectedDetailsExpanded);
  }

  void _setMapZoom(double zoom) {
    final currentZoom = _currentMapZoom;
    if (currentZoom != null &&
        _clusterZoomBucket(currentZoom) == _clusterZoomBucket(zoom)) {
      return;
    }

    setState(() => _currentMapZoom = zoom);
  }

  void _refreshMapViewport(LatLngBounds visibleBounds) {
    if (!mounted) {
      return;
    }

    final bounds = _loadBoundsFrom(visibleBounds);
    final viewportKey = bounds.cacheKey(precision: _viewportRenderPrecision);
    if (_lastRenderedViewportKey != viewportKey) {
      _lastRenderedViewportKey = viewportKey;
      setState(() {});
    }

    _viewportLoadDebounce?.cancel();
    _viewportLoadDebounce = Timer(_viewportLoadDebounceDuration, () {
      if (!mounted) {
        return;
      }
      _loadOrganizationsForBounds(bounds);
    });
  }

  OrganizationsBounds? _currentLoadBounds() {
    try {
      return _loadBoundsFrom(_mapController.camera.visibleBounds);
    } on Object {
      return null;
    }
  }

  OrganizationsBounds _loadBoundsFrom(LatLngBounds bounds) {
    final paddedBounds = _paddedLoadBounds(bounds);
    return OrganizationsBounds(
      north: paddedBounds.north,
      south: paddedBounds.south,
      east: paddedBounds.east,
      west: paddedBounds.west,
    );
  }

  LatLngBounds _paddedLoadBounds(LatLngBounds bounds) {
    final latPadding = (bounds.north - bounds.south).abs() * 0.35;
    final lonPadding = (bounds.east - bounds.west).abs() * 0.35;

    return LatLngBounds.unsafe(
      north: math.min(_mapBounds.north, bounds.north + latPadding),
      south: math.max(_mapBounds.south, bounds.south - latPadding),
      east: math.min(_mapBounds.east, bounds.east + lonPadding),
      west: math.max(_mapBounds.west, bounds.west - lonPadding),
    );
  }

  void _setSortMode(_OrganizationsSortMode sortMode) {
    if (_sortMode == sortMode) {
      return;
    }

    setState(() => _sortMode = sortMode);
  }

  void _setCategory(String? category) {
    if (_selectedCategory == category) {
      return;
    }

    setState(() {
      _selectedCategory = category;
      _selectedDetailsExpanded = false;
      _selectedOrganization = null;
    });
  }

  void _setChain(String? chainKey) {
    if (_selectedChainKey == chainKey) {
      return;
    }

    setState(() {
      _selectedChainKey = chainKey;
      _selectedDetailsExpanded = false;
      _selectedOrganization = null;
    });
  }

  void _setQualityFilter(_QualityFilterAction action) {
    final nextMinRating = switch (action.kind) {
      _QualityFilterActionKind.reset => null,
      _QualityFilterActionKind.minRating => action.minRating,
      _QualityFilterActionKind.minReviews => _minRatingFilter,
    };
    final nextMinReviews = switch (action.kind) {
      _QualityFilterActionKind.reset => null,
      _QualityFilterActionKind.minRating => _minReviewsFilter,
      _QualityFilterActionKind.minReviews => action.minReviews,
    };

    if (_minRatingFilter == nextMinRating &&
        _minReviewsFilter == nextMinReviews) {
      return;
    }

    setState(() {
      _minRatingFilter = nextMinRating;
      _minReviewsFilter = nextMinReviews;
      _selectedDetailsExpanded = false;
      _selectedOrganization = null;
      _hoveredOrganizationId = null;
    });
  }

  void _resetFilters() {
    if (_selectedCategory == null &&
        _selectedChainKey == null &&
        _minRatingFilter == null &&
        _minReviewsFilter == null) {
      return;
    }

    setState(() {
      _selectedCategory = null;
      _selectedChainKey = null;
      _minRatingFilter = null;
      _minReviewsFilter = null;
      _selectedDetailsExpanded = false;
      _selectedOrganization = null;
      _hoveredOrganizationId = null;
    });
  }

  Future<void> _analyzeReviews() async {
    final selectedOrganization = _selectedOrganization;
    if (_reviewsAnalysisLoading || selectedOrganization == null) {
      return;
    }

    setState(() => _reviewsAnalysisLoading = true);

    try {
      final analysis = await widget.analyzeReviews(selectedOrganization.id);
      if (!mounted) {
        return;
      }
      _showReviewsAnalysis(analysis);
    } on Object catch (error) {
      if (!mounted) {
        return;
      }
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Не удалось проанализировать отзывы: $error')),
      );
    } finally {
      if (mounted) {
        setState(() => _reviewsAnalysisLoading = false);
      }
    }
  }

  Future<void> _analyzeRadiusReviews() async {
    final selectedOrganization = _selectedOrganization;
    if (_radiusReviewsAnalysisLoading || selectedOrganization == null) {
      return;
    }

    setState(() => _radiusReviewsAnalysisLoading = true);

    try {
      final analysis = await widget.analyzeRadiusReviews(
        selectedOrganization.id,
        _radiusM,
      );
      if (!mounted) {
        return;
      }
      _showReviewsAnalysis(analysis);
    } on Object catch (error) {
      if (!mounted) {
        return;
      }
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Не удалось проанализировать радиус: $error')),
      );
    } finally {
      if (mounted) {
        setState(() => _radiusReviewsAnalysisLoading = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final viewData = _viewDataFor(_organizations);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Организации'),
        actions: [
          if (viewData.chains.isNotEmpty)
            _ChainFilterButton(
              chains: viewData.chains,
              selectedChainKey: _selectedChainKey,
              onSelected: _setChain,
            ),
          _CategoryFilterButton(
            categories: viewData.categories,
            selectedCategory: _selectedCategory,
            onSelected: _setCategory,
          ),
          _QualityFilterButton(
            minRating: _minRatingFilter,
            minReviews: _minReviewsFilter,
            onSelected: _setQualityFilter,
          ),
          PopupMenuButton<_OrganizationsSortMode>(
            tooltip: 'Сортировка',
            initialValue: _sortMode,
            icon: const Icon(Icons.swap_vert_rounded),
            onSelected: _setSortMode,
            itemBuilder: (context) {
              return _OrganizationsSortMode.values
                  .map(
                    (mode) => CheckedPopupMenuItem<_OrganizationsSortMode>(
                      value: mode,
                      checked: mode == _sortMode,
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(mode.icon, size: 18),
                          const SizedBox(width: 8),
                          Text(mode.label),
                        ],
                      ),
                    ),
                  )
                  .toList(growable: false);
            },
          ),
          IconButton(
            tooltip: 'Обновить',
            onPressed: _reload,
            icon: const Icon(Icons.refresh_rounded),
          ),
        ],
      ),
      body: _buildBody(viewData),
    );
  }

  Widget _buildBody(_OrganizationsViewData viewData) {
    if (_organizationsLoading && _organizations.isEmpty) {
      return const _LoadingState();
    }

    final error = _organizationsError;
    if (error != null && _organizations.isEmpty) {
      return _ErrorState(
        message: error.toString(),
        apiBaseUrl: widget.apiBaseUrl,
        onRetry: _reload,
      );
    }

    if (_organizations.isEmpty) {
      return _EmptyState(apiBaseUrl: widget.apiBaseUrl, onRetry: _reload);
    }

    final visibleOrganizations = viewData.visibleOrganizations;

    if (visibleOrganizations.isEmpty) {
      return _NoFilterResultsState(
        summary: _activeFiltersSummary(),
        onReset: _resetFilters,
      );
    }

    return _OrganizationsContent(
      listedOrganizations: viewData.listedOrganizations,
      mapOrganizations: viewData.mapOrganizations,
      selectedOrganization: viewData.selectedOrganization,
      selectedDetailsExpanded: _selectedDetailsExpanded,
      organizationsLoading: _organizationsLoading,
      reviewsAnalysisLoading: _reviewsAnalysisLoading,
      radiusReviewsAnalysisLoading: _radiusReviewsAnalysisLoading,
      selectedReviewDynamics: _reviewDynamicsFor(viewData.selectedOrganization),
      selectedReviewDynamicsLoading: _reviewDynamicsLoadingFor(
        viewData.selectedOrganization,
      ),
      selectedReviewDynamicsError: _reviewDynamicsErrorFor(
        viewData.selectedOrganization,
      ),
      radiusM: _radiusM,
      mapRadiusOnly: _mapRadiusOnly,
      hoveredOrganizationId: _hoveredOrganizationId,
      mapController: _mapController,
      currentZoom: _currentMapZoom,
      onZoomChanged: _setMapZoom,
      onRadiusChanged: _setRadiusM,
      onSelected: _selectOrganization,
      onClusterSelected: _openCluster,
      onMapTap: _handleMapTap,
      onViewportChanged: _refreshMapViewport,
      onToggleSelectedDetails: _toggleSelectedDetails,
      onClearSelected: _clearSelectedOrganization,
      onFocusSelected: _focusSelectedOrganization,
      onZoomIn: () => _changeMapZoomBy(1),
      onZoomOut: () => _changeMapZoomBy(-1),
      onRadiusSelected: _setRadiusM,
      onMapRadiusOnlyChanged: _setMapRadiusOnly,
      onHoverChanged: _setHoveredOrganizationId,
      onAnalyzeReviews: _analyzeReviews,
      onAnalyzeRadiusReviews: _analyzeRadiusReviews,
      onRefreshReviewDynamics: viewData.selectedOrganization == null
          ? null
          : () => _loadReviewDynamicsFor(
              viewData.selectedOrganization!.id,
              force: true,
            ),
      onOpenList: () => _showOrganizationsList(
        viewData.listedOrganizations,
        viewData.selectedOrganization,
      ),
    );
  }

  _OrganizationsViewData _viewDataFor(List<Organization> organizations) {
    _resetDerivedCachesIfNeeded(organizations);

    final categories = _categoriesFor(organizations);
    final chains = _chainsFor(organizations);
    final visibleOrganizations = _visibleOrganizationsFor(organizations);
    final selected = _selectedFrom(visibleOrganizations);
    final listedOrganizations = _radiusFilteredOrganizationsFor(
      visibleOrganizations,
      selected,
      _radiusM,
    );
    final mapSource = _mapRadiusOnly && selected != null
        ? listedOrganizations
        : visibleOrganizations;
    final mapOrganizations = _mapOrganizationsFor(mapSource, selected);

    return _OrganizationsViewData(
      categories: categories,
      chains: chains,
      visibleOrganizations: visibleOrganizations,
      listedOrganizations: listedOrganizations,
      mapOrganizations: mapOrganizations,
      selectedOrganization: selected,
    );
  }

  void _resetDerivedCachesIfNeeded(List<Organization> organizations) {
    if (identical(_cachedDerivedSource, organizations)) {
      return;
    }

    _cachedDerivedSource = organizations;
    _cachedCategoriesByOrganizationId.clear();
    _cachedChainKeysByOrganizationId.clear();
  }

  List<_CategoryOption> _categoriesFor(List<Organization> organizations) {
    if (identical(_cachedCategorySource, organizations)) {
      return _cachedCategories;
    }

    _cachedCategorySource = organizations;
    _cachedCategories = _categoryOptions(organizations);
    return _cachedCategories;
  }

  List<_ChainOption> _chainsFor(List<Organization> organizations) {
    if (identical(_cachedChainSource, organizations)) {
      return _cachedChains;
    }

    _cachedChainSource = organizations;
    _cachedChains = _chainOptions(organizations);
    return _cachedChains;
  }

  List<Organization> _visibleOrganizationsFor(
    List<Organization> organizations,
  ) {
    if (identical(_cachedVisibleSource, organizations) &&
        _cachedVisibleCategory == _selectedCategory &&
        _cachedVisibleChainKey == _selectedChainKey &&
        _cachedVisibleMinRating == _minRatingFilter &&
        _cachedVisibleMinReviews == _minReviewsFilter &&
        _cachedVisibleSortMode == _sortMode) {
      return _cachedVisibleOrganizations;
    }

    _cachedVisibleSource = organizations;
    _cachedVisibleCategory = _selectedCategory;
    _cachedVisibleChainKey = _selectedChainKey;
    _cachedVisibleMinRating = _minRatingFilter;
    _cachedVisibleMinReviews = _minReviewsFilter;
    _cachedVisibleSortMode = _sortMode;
    _cachedVisibleOrganizations = _sortOrganizations(
      _filterOrganizations(organizations),
    );
    return _cachedVisibleOrganizations;
  }

  List<Organization> _mapOrganizationsFor(
    List<Organization> organizations,
    Organization? selected,
  ) {
    final selectedId = selected?.id;
    if (identical(_cachedMapSource, organizations) &&
        _cachedMapSelectedId == selectedId) {
      return _cachedMapOrganizations;
    }

    _cachedMapSource = organizations;
    _cachedMapSelectedId = selectedId;
    _cachedMapOrganizations = organizations;
    return _cachedMapOrganizations;
  }

  List<Organization> _radiusFilteredOrganizationsFor(
    List<Organization> organizations,
    Organization? center,
    int radiusM,
  ) {
    if (center == null || organizations.isEmpty) {
      return organizations;
    }

    final centerPosition = _validPositionFor(center);
    if (centerPosition == null || radiusM <= 0) {
      return organizations;
    }

    final radius = radiusM.toDouble();
    final originalIndexById = <String, int>{
      for (var i = 0; i < organizations.length; i++) organizations[i].id: i,
    };
    final filtered = organizations
        .where((organization) {
          final position = _validPositionFor(organization);
          if (position == null) {
            return false;
          }

          return _radiusDistance(centerPosition, position) <= radius;
        })
        .toList(growable: false);

    return List<Organization>.of(filtered)..sort((left, right) {
      final leftPosition = _validPositionFor(left);
      final rightPosition = _validPositionFor(right);
      final leftDistance = leftPosition == null
          ? double.infinity
          : _radiusDistance(centerPosition, leftPosition);
      final rightDistance = rightPosition == null
          ? double.infinity
          : _radiusDistance(centerPosition, rightPosition);
      final distanceCompare = leftDistance.compareTo(rightDistance);
      if (distanceCompare != 0) {
        return distanceCompare;
      }

      return (originalIndexById[left.id] ?? 0).compareTo(
        originalIndexById[right.id] ?? 0,
      );
    });
  }

  List<Organization> _filterOrganizations(List<Organization> organizations) {
    final selectedCategory = _selectedCategory;
    final selectedChainKey = _selectedChainKey;
    final minRating = _minRatingFilter;
    final minReviews = _minReviewsFilter;
    final hasSelectedChain =
        selectedChainKey != null &&
        _cachedChains.any((chain) => chain.key == selectedChainKey);

    if (selectedCategory == null &&
        !hasSelectedChain &&
        minRating == null &&
        minReviews == null) {
      return organizations;
    }

    return organizations
        .where((organization) {
          if (selectedCategory != null &&
              !_categoriesOfCached(
                organization,
              ).any((category) => _sameCategory(category, selectedCategory))) {
            return false;
          }

          if (hasSelectedChain &&
              _chainKeyForCached(organization) != selectedChainKey) {
            return false;
          }

          if (minRating != null && _ratingAsNumber(organization) < minRating) {
            return false;
          }

          if (minReviews != null &&
              (organization.ratingCount ?? 0) < minReviews) {
            return false;
          }

          return true;
        })
        .toList(growable: false);
  }

  String _activeFiltersSummary() {
    final parts = <String>[
      if (_selectedCategory != null) 'категория: $_selectedCategory',
      if (_selectedChainKey != null)
        'сеть: ${_chainNameFromKey(_selectedChainKey!)}',
      if (_minRatingFilter != null)
        'рейтинг от ${_qualityRatingText(_minRatingFilter!)}',
      if (_minReviewsFilter != null) 'отзывов от $_minReviewsFilter',
    ];

    return parts.join(' · ');
  }

  List<_CategoryOption> _categoryOptions(List<Organization> organizations) {
    final countsByCategory = <String, int>{};
    for (final organization in organizations) {
      for (final category in _categoriesOfCached(organization)) {
        countsByCategory[category] = (countsByCategory[category] ?? 0) + 1;
      }
    }

    final options = countsByCategory.entries
        .map((entry) => _CategoryOption(name: entry.key, count: entry.value))
        .toList(growable: false);
    options.sort((left, right) => _compareText(left.name, right.name));
    return options;
  }

  List<_ChainOption> _chainOptions(List<Organization> organizations) {
    final countsByKey = <String, int>{};

    for (final organization in organizations) {
      final key = _chainKeyForCached(organization);
      if (key.isEmpty) {
        continue;
      }

      countsByKey[key] = (countsByKey[key] ?? 0) + 1;
    }

    final options = countsByKey.entries
        .where((entry) => entry.value >= _chainMinCount)
        .map(
          (entry) => _ChainOption(
            key: entry.key,
            name: _chainNameFromKey(entry.key),
            count: entry.value,
          ),
        )
        .toList(growable: false);

    options.sort((left, right) {
      final countResult = right.count.compareTo(left.count);
      if (countResult != 0) {
        return countResult;
      }
      return _compareText(left.name, right.name);
    });

    return options;
  }

  List<String> _categoriesOfCached(Organization organization) {
    return _cachedCategoriesByOrganizationId.putIfAbsent(
      organization.id,
      () => _categoriesOf(organization),
    );
  }

  String _chainKeyForCached(Organization organization) {
    return _cachedChainKeysByOrganizationId.putIfAbsent(
      organization.id,
      () => _chainKeyFor(organization),
    );
  }

  String _chainKeyFor(Organization organization) {
    final title = organization.shortTitle.trim().isNotEmpty
        ? organization.shortTitle
        : organization.displayTitle;
    return _normalizeChainKey(title);
  }

  String _normalizeChainKey(String title) {
    final normalized = title
        .toLowerCase()
        .replaceAll('ё', 'е')
        .replaceAll('&', ' ')
        .replaceAll(RegExp(r'''[.,;:!?()\[\]{}"«»'`’“”„/\\|+*_=\-]+'''), ' ');

    final tokens = normalized
        .split(RegExp(r'\s+'))
        .map((token) => token.trim())
        .where((token) {
          if (token.isEmpty || _chainStopWords.contains(token)) {
            return false;
          }
          return !_isDigitsOnly(token);
        })
        .take(3)
        .toList(growable: false);

    return tokens.join(' ');
  }

  bool _isDigitsOnly(String value) {
    return RegExp(r'^\d+$').hasMatch(value);
  }

  String _chainNameFromKey(String key) {
    return key.split(' ').map(_capitalizeChainToken).join(' ');
  }

  String _capitalizeChainToken(String token) {
    if (token.length <= 3) {
      return token.toUpperCase();
    }
    return token.substring(0, 1).toUpperCase() + token.substring(1);
  }

  List<String> _categoriesOf(Organization organization) {
    final categories = organization.categoryNames;
    if (categories.isEmpty) {
      return const [];
    }

    final parts = categories
        .expand((category) => category.split(RegExp(r'[,;|/]+')))
        .map(_cleanCategory)
        .where((category) => category.isNotEmpty)
        .toSet()
        .toList(growable: false);

    if (parts.isEmpty) {
      return const [];
    }

    return parts;
  }

  String _cleanCategory(String category) {
    return category
        .replaceAll(RegExp(r'''^[\s\[\]'"«»]+|[\s\[\]'"«»]+$'''), '')
        .trim();
  }

  bool _sameCategory(String left, String right) {
    return left.trim().toLowerCase() == right.trim().toLowerCase();
  }

  List<Organization> _sortOrganizations(List<Organization> organizations) {
    if (_sortMode == _OrganizationsSortMode.source) {
      return organizations;
    }

    final sorted = List<Organization>.of(organizations);
    sorted.sort((left, right) {
      return switch (_sortMode) {
        _OrganizationsSortMode.source => 0,
        _OrganizationsSortMode.title => _compareText(
          left.displayTitle,
          right.displayTitle,
        ),
        _OrganizationsSortMode.category => _compareCategory(left, right),
        _OrganizationsSortMode.rating => _compareRating(left, right),
        _OrganizationsSortMode.reviews => _compareReviews(left, right),
      };
    });

    return sorted;
  }

  int _compareCategory(Organization left, Organization right) {
    final result = _compareText(left.categoryLabel, right.categoryLabel);
    if (result != 0) {
      return result;
    }
    return _compareText(left.displayTitle, right.displayTitle);
  }

  int _compareRating(Organization left, Organization right) {
    final result = _ratingAsNumber(right).compareTo(_ratingAsNumber(left));
    if (result != 0) {
      return result;
    }
    return _compareText(left.displayTitle, right.displayTitle);
  }

  int _compareReviews(Organization left, Organization right) {
    final result = (right.ratingCount ?? -1).compareTo(left.ratingCount ?? -1);
    if (result != 0) {
      return result;
    }
    return _compareText(left.displayTitle, right.displayTitle);
  }

  double _ratingAsNumber(Organization organization) {
    return double.tryParse(organization.ratingValue.replaceAll(',', '.')) ?? -1;
  }

  int _compareText(
    String left,
    String right, {
    String fallbackLeft = '',
    String fallbackRight = '',
  }) {
    final normalizedLeft = left.trim().toLowerCase();
    final normalizedRight = right.trim().toLowerCase();

    if (normalizedLeft.isEmpty && normalizedRight.isEmpty) {
      if (fallbackLeft.isEmpty && fallbackRight.isEmpty) {
        return 0;
      }
      return _compareText(fallbackLeft, fallbackRight);
    }
    if (normalizedLeft.isEmpty) {
      return 1;
    }
    if (normalizedRight.isEmpty) {
      return -1;
    }

    final result = normalizedLeft.compareTo(normalizedRight);
    if (result != 0) {
      return result;
    }

    return left.compareTo(right);
  }

  Organization? _selectedFrom(List<Organization> organizations) {
    final selected = _selectedOrganization;
    final selectedId = selected?.id;
    if (selected == null || selectedId == null) {
      return null;
    }

    if (identical(_cachedSelectedSource, organizations) &&
        _cachedSelectedId == selectedId) {
      return _cachedSelectedOrganization;
    }

    _cachedSelectedSource = organizations;
    _cachedSelectedId = selectedId;
    _cachedSelectedOrganization = _findSelectedFrom(organizations, selectedId);
    return _cachedSelectedOrganization;
  }

  Organization? _findSelectedFrom(
    List<Organization> organizations,
    String selectedId,
  ) {
    for (final organization in organizations) {
      if (organization.id == selectedId) {
        return organization;
      }
    }
    return null;
  }

  void _showOrganizationsList(
    List<Organization> organizations,
    Organization? selected,
  ) {
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (context) {
        return SafeArea(
          child: SizedBox(
            height: MediaQuery.sizeOf(context).height * 0.78,
            child: _OrganizationsList(
              organizations: organizations,
              selectedOrganization: selected,
              hoveredOrganizationId: _hoveredOrganizationId,
              nearestRanksById: _nearestRanksByIdFor(organizations, selected),
              onSelected: (organization) {
                Navigator.of(context).pop();
                _selectOrganization(organization);
              },
              onHoverChanged: _setHoveredOrganizationId,
            ),
          ),
        );
      },
    );
  }

  void _showReviewsAnalysis(String analysis) {
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (context) {
        return SafeArea(
          child: FractionallySizedBox(
            heightFactor: 0.78,
            child: _ReviewsAnalysisSheet(analysis: analysis),
          ),
        );
      },
    );
  }
}

class _ReviewsAnalysisSheet extends StatelessWidget {
  const _ReviewsAnalysisSheet({required this.analysis});

  final String analysis;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final textTheme = Theme.of(context).textTheme;

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.auto_awesome_rounded, color: colorScheme.primary),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  'Аналитика отзывов',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: textTheme.titleMedium?.copyWith(
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              IconButton(
                tooltip: 'Закрыть',
                onPressed: () => Navigator.of(context).pop(),
                icon: const Icon(Icons.close_rounded),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Expanded(
            child: SingleChildScrollView(
              child: Align(
                alignment: Alignment.centerLeft,
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    color: colorScheme.primaryContainer,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Padding(
                    padding: const EdgeInsets.all(14),
                    child: SelectableText(
                      analysis,
                      style: textTheme.bodyMedium?.copyWith(
                        color: colorScheme.onPrimaryContainer,
                        height: 1.38,
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _ChainFilterButton extends StatelessWidget {
  const _ChainFilterButton({
    required this.chains,
    required this.selectedChainKey,
    required this.onSelected,
  });

  final List<_ChainOption> chains;
  final String? selectedChainKey;
  final ValueChanged<String?> onSelected;

  @override
  Widget build(BuildContext context) {
    final activeChainKey = chains.any((chain) => chain.key == selectedChainKey)
        ? selectedChainKey
        : null;
    final hasFilter = activeChainKey != null;

    return PopupMenuButton<String>(
      tooltip: 'Выбор сети',
      initialValue: activeChainKey ?? _allChainsValue,
      icon: Icon(
        hasFilter ? Icons.storefront_rounded : Icons.account_tree_rounded,
      ),
      onSelected: chains.isEmpty
          ? null
          : (value) {
              onSelected(value == _allChainsValue ? null : value);
            },
      itemBuilder: (context) {
        return [
          CheckedPopupMenuItem<String>(
            value: _allChainsValue,
            checked: activeChainKey == null,
            child: const Text('Все сети'),
          ),
          const PopupMenuDivider(),
          ...chains.map(
            (chain) => CheckedPopupMenuItem<String>(
              value: chain.key,
              checked: activeChainKey == chain.key,
              child: Text('${chain.name} (${chain.count})'),
            ),
          ),
        ];
      },
    );
  }
}

class _CategoryFilterButton extends StatelessWidget {
  const _CategoryFilterButton({
    required this.categories,
    required this.selectedCategory,
    required this.onSelected,
  });

  final List<_CategoryOption> categories;
  final String? selectedCategory;
  final ValueChanged<String?> onSelected;

  @override
  Widget build(BuildContext context) {
    final hasFilter = selectedCategory != null;

    return PopupMenuButton<String>(
      tooltip: 'Категория',
      initialValue: selectedCategory ?? _allCategoriesValue,
      icon: Icon(hasFilter ? Icons.category_rounded : Icons.search_rounded),
      onSelected: categories.isEmpty
          ? null
          : (value) {
              onSelected(value == _allCategoriesValue ? null : value);
            },
      itemBuilder: (context) {
        return [
          CheckedPopupMenuItem<String>(
            value: _allCategoriesValue,
            checked: selectedCategory == null,
            child: const Text('Все категории'),
          ),
          const PopupMenuDivider(),
          ...categories.map(
            (category) => CheckedPopupMenuItem<String>(
              value: category.name,
              checked: selectedCategory == category.name,
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(_categoryIcon(category.name), size: 18),
                  const SizedBox(width: 8),
                  Flexible(child: Text('${category.name} (${category.count})')),
                ],
              ),
            ),
          ),
        ];
      },
    );
  }
}

class _QualityFilterButton extends StatelessWidget {
  const _QualityFilterButton({
    required this.minRating,
    required this.minReviews,
    required this.onSelected,
  });

  final double? minRating;
  final int? minReviews;
  final ValueChanged<_QualityFilterAction> onSelected;

  @override
  Widget build(BuildContext context) {
    final hasFilter = minRating != null || minReviews != null;

    return PopupMenuButton<_QualityFilterAction>(
      tooltip: hasFilter ? _qualityFilterTooltip() : 'Фильтр качества',
      icon: Icon(
        hasFilter ? Icons.filter_alt_rounded : Icons.filter_alt_outlined,
      ),
      onSelected: onSelected,
      itemBuilder: (context) {
        return [
          PopupMenuItem<_QualityFilterAction>(
            value: const _QualityFilterAction.reset(),
            enabled: hasFilter,
            child: const Text('Сбросить качество'),
          ),
          const PopupMenuDivider(),
          const PopupMenuItem<_QualityFilterAction>(
            enabled: false,
            child: Text('Рейтинг'),
          ),
          CheckedPopupMenuItem<_QualityFilterAction>(
            value: const _QualityFilterAction.minRating(null),
            checked: minRating == null,
            child: const Text('Любой рейтинг'),
          ),
          for (final value in const [4.0, 4.5, 4.8])
            CheckedPopupMenuItem<_QualityFilterAction>(
              value: _QualityFilterAction.minRating(value),
              checked: minRating == value,
              child: Text('${_qualityRatingText(value)}+'),
            ),
          const PopupMenuDivider(),
          const PopupMenuItem<_QualityFilterAction>(
            enabled: false,
            child: Text('Отзывы'),
          ),
          CheckedPopupMenuItem<_QualityFilterAction>(
            value: const _QualityFilterAction.minReviews(null),
            checked: minReviews == null,
            child: const Text('Любое число отзывов'),
          ),
          for (final value in const [10, 50, 100])
            CheckedPopupMenuItem<_QualityFilterAction>(
              value: _QualityFilterAction.minReviews(value),
              checked: minReviews == value,
              child: Text('$value+ отзывов'),
            ),
        ];
      },
    );
  }

  String _qualityFilterTooltip() {
    final parts = <String>[
      if (minRating != null) 'рейтинг ${_qualityRatingText(minRating!)}+',
      if (minReviews != null) 'отзывы $minReviews+',
    ];

    return 'Фильтр качества: ${parts.join(', ')}';
  }
}

class _OrganizationsContent extends StatelessWidget {
  const _OrganizationsContent({
    required this.listedOrganizations,
    required this.mapOrganizations,
    required this.selectedOrganization,
    required this.selectedDetailsExpanded,
    required this.organizationsLoading,
    required this.reviewsAnalysisLoading,
    required this.radiusReviewsAnalysisLoading,
    required this.selectedReviewDynamics,
    required this.selectedReviewDynamicsLoading,
    required this.selectedReviewDynamicsError,
    required this.radiusM,
    required this.mapRadiusOnly,
    required this.hoveredOrganizationId,
    required this.mapController,
    required this.currentZoom,
    required this.onZoomChanged,
    required this.onRadiusChanged,
    required this.onSelected,
    required this.onClusterSelected,
    required this.onMapTap,
    required this.onViewportChanged,
    required this.onToggleSelectedDetails,
    required this.onClearSelected,
    required this.onFocusSelected,
    required this.onZoomIn,
    required this.onZoomOut,
    required this.onRadiusSelected,
    required this.onMapRadiusOnlyChanged,
    required this.onHoverChanged,
    required this.onAnalyzeReviews,
    required this.onAnalyzeRadiusReviews,
    required this.onRefreshReviewDynamics,
    required this.onOpenList,
  });

  final List<Organization> listedOrganizations;
  final List<Organization> mapOrganizations;
  final Organization? selectedOrganization;
  final bool selectedDetailsExpanded;
  final bool organizationsLoading;
  final bool reviewsAnalysisLoading;
  final bool radiusReviewsAnalysisLoading;
  final ReviewDynamics? selectedReviewDynamics;
  final bool selectedReviewDynamicsLoading;
  final Object? selectedReviewDynamicsError;
  final int radiusM;
  final bool mapRadiusOnly;
  final String? hoveredOrganizationId;
  final MapController mapController;
  final double? currentZoom;
  final ValueChanged<double> onZoomChanged;
  final ValueChanged<int> onRadiusChanged;
  final ValueChanged<Organization> onSelected;
  final ValueChanged<_MapCluster> onClusterSelected;
  final ValueChanged<LatLng> onMapTap;
  final ValueChanged<LatLngBounds> onViewportChanged;
  final VoidCallback onToggleSelectedDetails;
  final VoidCallback onClearSelected;
  final VoidCallback onFocusSelected;
  final VoidCallback onZoomIn;
  final VoidCallback onZoomOut;
  final ValueChanged<int> onRadiusSelected;
  final ValueChanged<bool> onMapRadiusOnlyChanged;
  final ValueChanged<String?> onHoverChanged;
  final VoidCallback onAnalyzeReviews;
  final VoidCallback onAnalyzeRadiusReviews;
  final VoidCallback? onRefreshReviewDynamics;
  final VoidCallback onOpenList;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final selectedId = selectedOrganization?.id;
        final selectedPosition = selectedOrganization == null
            ? null
            : _validPositionFor(selectedOrganization!);
        final radiusNeighborDistances = selectedId == null
            ? const <double>[]
            : listedOrganizations
                  .where((organization) => organization.id != selectedId)
                  .map((organization) {
                    final position = _validPositionFor(organization);
                    if (selectedPosition == null || position == null) {
                      return double.infinity;
                    }
                    return _radiusDistance(selectedPosition, position);
                  })
                  .where((distance) => distance.isFinite)
                  .toList(growable: false);
        final radiusResultCount = selectedId == null
            ? listedOrganizations.length
            : radiusNeighborDistances.length;
        final nearestRadiusDistanceM = radiusNeighborDistances.isEmpty
            ? null
            : radiusNeighborDistances.reduce(math.min);
        final hasRadiusResults = selectedId == null || radiusResultCount > 0;
        final nearestRanksById = _nearestRanksByIdFor(
          listedOrganizations,
          selectedOrganization,
        );
        final map = _OrganizationsMap(
          organizations: mapOrganizations,
          selectedOrganization: selectedOrganization,
          hoveredOrganizationId: hoveredOrganizationId,
          nearestRanksById: nearestRanksById,
          radiusM: radiusM,
          markersEnabled: _mapMarkersEnabled,
          mapController: mapController,
          currentZoom: currentZoom,
          onZoomChanged: onZoomChanged,
          onSelected: onSelected,
          onClusterSelected: onClusterSelected,
          onMapTap: onMapTap,
          onViewportChanged: onViewportChanged,
          onHoverChanged: onHoverChanged,
        );
        final mapWithControls = Stack(
          children: [
            Positioned.fill(child: map),
            Positioned(
              right: 12,
              top: 12,
              child: _MapControls(
                hasSelectedOrganization: selectedOrganization != null,
                onZoomIn: onZoomIn,
                onZoomOut: onZoomOut,
                onFocusSelected: onFocusSelected,
              ),
            ),
            if (organizationsLoading)
              const Positioned(
                top: 12,
                left: 0,
                right: 0,
                child: Center(child: _ViewportLoadingBadge()),
              ),
            if (selectedOrganization != null)
              Positioned(
                left: 12,
                top: 12,
                child: _RadiusControlCard(
                  radiusM: radiusM,
                  count: radiusResultCount,
                  nearestDistanceM: nearestRadiusDistanceM,
                  radiusOnly: mapRadiusOnly,
                  onRadiusChanged: onRadiusChanged,
                  onRadiusOnlyChanged: onMapRadiusOnlyChanged,
                ),
              ),
            if (!_mapPanelEnabled && selectedOrganization != null)
              Positioned(
                right: 12,
                bottom: 12,
                left: constraints.maxWidth < 520 ? 12 : null,
                child: ConstrainedBox(
                  constraints: BoxConstraints(
                    maxWidth: constraints.maxWidth.isFinite
                        ? math.max(
                            0.0,
                            math.min(380.0, constraints.maxWidth - 24),
                          )
                        : 380.0,
                  ),
                  child: _SelectedOrganizationCard(
                    organization: selectedOrganization!,
                    expanded: selectedDetailsExpanded,
                    nearbyCount: radiusResultCount,
                    nearestDistanceM: nearestRadiusDistanceM,
                    reviewsAnalysisLoading: reviewsAnalysisLoading,
                    radiusReviewsAnalysisLoading: radiusReviewsAnalysisLoading,
                    radiusM: radiusM,
                    reviewDynamics: selectedReviewDynamics,
                    reviewDynamicsLoading: selectedReviewDynamicsLoading,
                    reviewDynamicsError: selectedReviewDynamicsError,
                    onToggle: onToggleSelectedDetails,
                    onClose: onClearSelected,
                    onAnalyzeReviews: onAnalyzeReviews,
                    onAnalyzeRadiusReviews: onAnalyzeRadiusReviews,
                    onRefreshReviewDynamics: onRefreshReviewDynamics,
                  ),
                ),
              ),
          ],
        );

        if (constraints.maxWidth >= 900) {
          if (!_mapPanelEnabled) {
            return mapWithControls;
          }

          return Row(
            children: [
              Expanded(child: mapWithControls),
              SizedBox(
                width: 380,
                child: _DesktopPanel(
                  organizations: listedOrganizations,
                  selectedOrganization: selectedOrganization,
                  selectedDetailsExpanded: selectedDetailsExpanded,
                  reviewsAnalysisLoading: reviewsAnalysisLoading,
                  radiusReviewsAnalysisLoading: radiusReviewsAnalysisLoading,
                  selectedReviewDynamics: selectedReviewDynamics,
                  selectedReviewDynamicsLoading: selectedReviewDynamicsLoading,
                  selectedReviewDynamicsError: selectedReviewDynamicsError,
                  radiusM: radiusM,
                  hasRadiusResults: hasRadiusResults,
                  hoveredOrganizationId: hoveredOrganizationId,
                  nearbyCount: radiusResultCount,
                  nearestDistanceM: nearestRadiusDistanceM,
                  nearestRanksById: nearestRanksById,
                  onSelected: onSelected,
                  onHoverChanged: onHoverChanged,
                  onToggleSelectedDetails: onToggleSelectedDetails,
                  onClearSelected: onClearSelected,
                  onRadiusSelected: onRadiusSelected,
                  onAnalyzeReviews: onAnalyzeReviews,
                  onAnalyzeRadiusReviews: onAnalyzeRadiusReviews,
                  onRefreshReviewDynamics: onRefreshReviewDynamics,
                ),
              ),
            ],
          );
        }

        return Stack(
          children: [
            Positioned.fill(child: mapWithControls),
            if (_mapPanelEnabled)
              Positioned(
                left: 12,
                right: 12,
                bottom: 12,
                child: _CompactPanel(
                  count: listedOrganizations.length,
                  selectedOrganization: selectedOrganization,
                  selectedDetailsExpanded: selectedDetailsExpanded,
                  reviewsAnalysisLoading: reviewsAnalysisLoading,
                  radiusReviewsAnalysisLoading: radiusReviewsAnalysisLoading,
                  selectedReviewDynamics: selectedReviewDynamics,
                  selectedReviewDynamicsLoading: selectedReviewDynamicsLoading,
                  selectedReviewDynamicsError: selectedReviewDynamicsError,
                  radiusM: radiusM,
                  hasRadiusResults: hasRadiusResults,
                  nearbyCount: radiusResultCount,
                  nearestDistanceM: nearestRadiusDistanceM,
                  onToggleSelectedDetails: onToggleSelectedDetails,
                  onClearSelected: onClearSelected,
                  onRadiusSelected: onRadiusSelected,
                  onAnalyzeReviews: onAnalyzeReviews,
                  onAnalyzeRadiusReviews: onAnalyzeRadiusReviews,
                  onRefreshReviewDynamics: onRefreshReviewDynamics,
                  onOpenList: onOpenList,
                ),
              ),
          ],
        );
      },
    );
  }
}

class _OrganizationsMap extends StatelessWidget {
  const _OrganizationsMap({
    required this.organizations,
    required this.selectedOrganization,
    required this.hoveredOrganizationId,
    required this.nearestRanksById,
    required this.radiusM,
    required this.markersEnabled,
    required this.mapController,
    required this.currentZoom,
    required this.onZoomChanged,
    required this.onSelected,
    required this.onClusterSelected,
    required this.onMapTap,
    required this.onViewportChanged,
    required this.onHoverChanged,
  });

  final List<Organization> organizations;
  final Organization? selectedOrganization;
  final String? hoveredOrganizationId;
  final Map<String, int> nearestRanksById;
  final int radiusM;
  final bool markersEnabled;
  final MapController mapController;
  final double? currentZoom;
  final ValueChanged<double> onZoomChanged;
  final ValueChanged<Organization> onSelected;
  final ValueChanged<_MapCluster> onClusterSelected;
  final ValueChanged<LatLng> onMapTap;
  final ValueChanged<LatLngBounds> onViewportChanged;
  final ValueChanged<String?> onHoverChanged;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final viewportSize = _viewportSizeFor(context, constraints);
        final minZoom = _containedMapMinZoomFor(viewportSize);
        final initialZoom = math.max(_initialZoomFor(organizations), minZoom);
        final radiusCircles = _radiusCirclesForMap();

        return FlutterMap(
          mapController: mapController,
          options: MapOptions(
            initialCenter: _centerFor(organizations),
            initialZoom: initialZoom,
            minZoom: minZoom,
            maxZoom: 17,
            cameraConstraint: CameraConstraint.contain(bounds: _mapBounds),
            interactionOptions: const InteractionOptions(
              flags: InteractiveFlag.all & ~InteractiveFlag.rotate,
            ),
            onTap: (_, point) => onMapTap(point),
            onPositionChanged: (camera, hasGesture) {
              final previousZoom = currentZoom;
              if (hasGesture &&
                  (previousZoom == null ||
                      _clusterZoomBucket(previousZoom) !=
                          _clusterZoomBucket(camera.zoom))) {
                onZoomChanged(camera.zoom);
              }
            },
            onMapEvent: (event) {
              if (event is MapEventMoveEnd) {
                onViewportChanged(mapController.camera.visibleBounds);
              }
            },
          ),
          children: [
            TileLayer(
              urlTemplate: _mapTileUrl,
              fallbackUrl: _mapTileFallbackUrl(),
              subdomains: _mapTileSubdomains(),
              retinaMode: false,
              panBuffer: 1,
              keepBuffer: 2,
              tileBounds: _mapBounds,
              userAgentPackageName: 'com.example.ui',
            ),
            if (radiusCircles.isNotEmpty) CircleLayer(circles: radiusCircles),
            if (markersEnabled)
              Builder(
                builder: (context) {
                  return MarkerLayer(
                    markers: _markersForMap(MapCamera.maybeOf(context)),
                  );
                },
              ),
            const RichAttributionWidget(
              attributions: [TextSourceAttribution(_mapTileAttribution)],
            ),
          ],
        );
      },
    );
  }

  Size _viewportSizeFor(BuildContext context, BoxConstraints constraints) {
    final fallbackSize = MediaQuery.sizeOf(context);
    return Size(
      constraints.maxWidth.isFinite ? constraints.maxWidth : fallbackSize.width,
      constraints.maxHeight.isFinite
          ? constraints.maxHeight
          : fallbackSize.height,
    );
  }

  double _containedMapMinZoomFor(Size viewportSize) {
    if (!viewportSize.width.isFinite ||
        !viewportSize.height.isFinite ||
        viewportSize.width <= 0 ||
        viewportSize.height <= 0) {
      return _defaultMinMapZoom;
    }

    const crs = Epsg3857();
    final northEast = crs.latLngToOffset(_mapBounds.northEast, 0);
    final southWest = crs.latLngToOffset(_mapBounds.southWest, 0);
    final boundsWidth = (northEast.dx - southWest.dx).abs();
    final boundsHeight = (northEast.dy - southWest.dy).abs();

    if (boundsWidth <= 0 || boundsHeight <= 0) {
      return _defaultMinMapZoom;
    }

    final minWidthZoom = math.log(viewportSize.width / boundsWidth) / math.ln2;
    final minHeightZoom =
        math.log(viewportSize.height / boundsHeight) / math.ln2;

    return math.max(
      _defaultMinMapZoom,
      math.max(minWidthZoom, minHeightZoom) + _containedMapZoomPadding,
    );
  }

  List<Marker> _markersForMap(MapCamera? camera) {
    return _markerItemsForMap(
      camera,
    ).map(_markerForItem).toList(growable: false);
  }

  List<CircleMarker<Object>> _radiusCirclesForMap() {
    final selected = selectedOrganization;
    if (selected == null) {
      return const [];
    }

    final position = _validPositionFor(selected);
    if (position == null) {
      return const [];
    }

    return [
      CircleMarker<Object>(
        point: position,
        radius: radiusM.toDouble(),
        useRadiusInMeter: true,
        color: _radiusFillColor,
        borderStrokeWidth: 2,
        borderColor: _radiusStrokeColor,
      ),
    ];
  }

  Marker _markerForItem(_MapMarkerItem item) {
    final cluster = item.cluster;
    final organization = cluster.representative;
    final anchored = item.mode != _MapMarkerMode.cluster && cluster.isSingle;
    final size = _markerSizeFor(item);
    final cardWidth = size.width;
    final child = switch (item.mode) {
      _MapMarkerMode.cluster => KeyedSubtree(
        key: ValueKey('cluster-${cluster.key}-${cluster.count}'),
        child: _markerTapTarget(
          tooltip: '${cluster.count}',
          onTap: () => onClusterSelected(cluster),
          child: _clusterMarkerContent(cluster.count),
        ),
      ),
      _MapMarkerMode.pinOnly => KeyedSubtree(
        key: ValueKey('pin-only-${organization.id}'),
        child: _markerTapTarget(
          tooltip: organization.displayTitle,
          onTap: cluster.isSingle
              ? () => onSelected(organization)
              : () => onClusterSelected(cluster),
          hoverOrganizationId: cluster.isSingle ? organization.id : null,
          onHoverChanged: onHoverChanged,
          child: _pinOnlyMarkerContent(
            selected: item.selected,
            hovered: item.hovered,
          ),
        ),
      ),
      _MapMarkerMode.neighborPin => KeyedSubtree(
        key: ValueKey('neighbor-pin-${organization.id}'),
        child: _markerTapTarget(
          tooltip: organization.displayTitle,
          onTap: () => onSelected(organization),
          hoverOrganizationId: organization.id,
          onHoverChanged: onHoverChanged,
          child: _neighborPinMarkerContent(
            organization,
            item.hovered,
            item.rank,
            cardWidth,
          ),
        ),
      ),
      _MapMarkerMode.compact => KeyedSubtree(
        key: ValueKey('compact-${cluster.key}-${cluster.count}'),
        child: _markerTapTarget(
          tooltip: organization.displayTitle,
          onTap: cluster.isSingle
              ? () => onSelected(organization)
              : () => onClusterSelected(cluster),
          hoverOrganizationId: cluster.isSingle ? organization.id : null,
          onHoverChanged: onHoverChanged,
          child: _compactMarkerContent(
            organization,
            cluster.isSingle ? null : cluster.count,
            item.selected,
            item.hovered,
            item.rank,
            cardWidth,
          ),
        ),
      ),
      _MapMarkerMode.detailed => KeyedSubtree(
        key: ValueKey('detailed-${organization.id}'),
        child: _markerTapTarget(
          tooltip: organization.displayTitle,
          onTap: () => onSelected(organization),
          hoverOrganizationId: organization.id,
          onHoverChanged: onHoverChanged,
          child: _detailedMarkerContent(
            organization,
            item.selected,
            item.hovered,
            item.rank,
            cardWidth,
          ),
        ),
      ),
    };

    return Marker(
      point: cluster.center,
      width: size.width,
      height: size.height,
      alignment: anchored ? Alignment.bottomCenter : Alignment.center,
      child: child,
    );
  }

  Size _markerSizeFor(_MapMarkerItem item) {
    return switch (item.mode) {
      _MapMarkerMode.cluster => const Size.square(64),
      _MapMarkerMode.pinOnly => const Size(38, 44),
      _MapMarkerMode.neighborPin => Size(
        _neighborPinMarkerWidthFor(
          item.cluster.representative,
          hasRank: item.rank != null,
        ),
        62,
      ),
      _MapMarkerMode.compact =>
        item.cluster.isSingle
            ? Size(
                _organizationMarkerWidthFor(
                  item.cluster.representative,
                  _MapMarkerMode.compact,
                  hasRank: item.rank != null,
                  showKpis: item.selected || item.hovered,
                ),
                72,
              )
            : const Size(132, 46),
      _MapMarkerMode.detailed => Size(
        _organizationMarkerWidthFor(
          item.cluster.representative,
          _MapMarkerMode.detailed,
          hasRank: item.rank != null,
          showKpis: true,
        ),
        78,
      ),
    };
  }

  double _organizationMarkerWidthFor(
    Organization organization,
    _MapMarkerMode mode, {
    required bool hasRank,
    bool showKpis = false,
  }) {
    final title = organization.displayTitle.trim();
    final fontSize = 11.5;
    final chromeWidth = mode == _MapMarkerMode.detailed
        ? _detailedMapLabelChromeWidth
        : _compactMapLabelChromeWidth;
    final rankWidth = hasRank ? 34.0 : 0.0;
    final kpiWidth = showKpis ? _mapMarkerKpiWidthFor(organization) : 0.0;
    final measuredWidth = _measureMapMarkerTitleWidth(title, fontSize);

    return (chromeWidth + rankWidth + kpiWidth + measuredWidth)
        .clamp(_minMapLabelWidth, _maxMapLabelWidth)
        .toDouble();
  }

  double _neighborPinMarkerWidthFor(
    Organization organization, {
    required bool hasRank,
  }) {
    final title = organization.displayTitle.trim();
    final rankWidth = hasRank ? 36.0 : 0.0;
    final measuredWidth = _measureMapMarkerTitleWidth(title, 11);

    return (52.0 + rankWidth + measuredWidth).clamp(118.0, 190.0).toDouble();
  }

  double _measureMapMarkerTitleWidth(String title, double fontSize) {
    final cacheKey = '$fontSize|$title';
    final cachedWidth = _mapMarkerTitleWidthCache[cacheKey];
    if (cachedWidth != null) {
      return cachedWidth;
    }

    final painter = TextPainter(
      text: TextSpan(
        text: title,
        style: TextStyle(fontSize: fontSize, fontWeight: FontWeight.w800),
      ),
      maxLines: 1,
      textDirection: TextDirection.ltr,
    )..layout();

    final width = painter.width.ceilToDouble() + 12;
    if (_mapMarkerTitleWidthCache.length >= _mapMarkerMeasurementCacheLimit) {
      _mapMarkerTitleWidthCache.clear();
    }
    _mapMarkerTitleWidthCache[cacheKey] = width;
    return width;
  }

  double _mapMarkerKpiWidthFor(Organization organization) {
    final texts = _mapMarkerKpiTextsFor(organization);
    if (texts.isEmpty) {
      return 0;
    }

    final badgesWidth = texts
        .map(_measureMapMarkerKpiBadgeWidth)
        .fold<double>(0, (sum, width) => sum + width);
    return 5 + badgesWidth + (texts.length - 1) * 4;
  }

  double _measureMapMarkerKpiBadgeWidth(String text) {
    final cachedWidth = _mapMarkerKpiBadgeWidthCache[text];
    if (cachedWidth != null) {
      return cachedWidth;
    }

    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: const TextStyle(fontSize: 9.5, fontWeight: FontWeight.w800),
      ),
      maxLines: 1,
      textDirection: TextDirection.ltr,
    )..layout();

    final width = 8 + painter.width.ceilToDouble();
    if (_mapMarkerKpiBadgeWidthCache.length >=
        _mapMarkerMeasurementCacheLimit) {
      _mapMarkerKpiBadgeWidthCache.clear();
    }
    _mapMarkerKpiBadgeWidthCache[text] = width;
    return width;
  }

  List<_MapMarkerItem> _markerItemsForMap(MapCamera? camera) {
    final selectedId = selectedOrganization?.id;
    final visible = _organizationsInViewport();
    final zoom = currentZoom ?? _initialZoomFor(visible);
    final clusters = _clustersForZoom(visible);
    final detailedIds = _detailedOrganizationIdsFor(clusters, zoom, selectedId);
    final items = <_MapMarkerItem>[];

    for (final cluster in clusters) {
      final selected =
          cluster.isSingle && cluster.representative.id == selectedId;
      final hovered =
          cluster.isSingle &&
          cluster.representative.id == hoveredOrganizationId;
      final mode = _modeForCluster(cluster, zoom, detailedIds);
      items.add(
        _MapMarkerItem(
          cluster: cluster,
          mode: mode,
          selected: selected,
          hovered: hovered,
          rank: cluster.isSingle
              ? nearestRanksById[cluster.representative.id]
              : null,
        ),
      );
    }

    return _avoidOverlappingMarkerItems(
      _limitMarkerItems(items, selectedId),
      camera,
    );
  }

  Set<String> _detailedOrganizationIdsFor(
    List<_MapCluster> clusters,
    double zoom,
    String? selectedId,
  ) {
    if (zoom < 16) {
      return const {};
    }

    final singles =
        clusters
            .where((cluster) => cluster.isSingle)
            .map((cluster) => cluster.representative)
            .toList(growable: false)
          ..sort((left, right) {
            if (left.id == selectedId && right.id != selectedId) {
              return -1;
            }
            if (right.id == selectedId && left.id != selectedId) {
              return 1;
            }
            return _markerRepresentativeScore(
              right,
            ).compareTo(_markerRepresentativeScore(left));
          });

    return singles
        .take(_maxDetailedMapMarkers)
        .map((organization) => organization.id)
        .toSet();
  }

  _MapMarkerMode _modeForCluster(
    _MapCluster cluster,
    double zoom,
    Set<String> detailedIds,
  ) {
    if (!cluster.isSingle) {
      return zoom < 13 ? _MapMarkerMode.cluster : _MapMarkerMode.compact;
    }

    if (zoom >= 16 && detailedIds.contains(cluster.representative.id)) {
      return _MapMarkerMode.detailed;
    }
    return _MapMarkerMode.compact;
  }

  List<_MapMarkerItem> _limitMarkerItems(
    List<_MapMarkerItem> items,
    String? selectedId,
  ) {
    if (items.length <= _maxMapMarkers) {
      return _paintOrderedMarkerItems(items);
    }

    final sorted = List<_MapMarkerItem>.of(items)
      ..sort((left, right) {
        return _markerItemPriority(
          right,
          selectedId,
        ).compareTo(_markerItemPriority(left, selectedId));
      });

    final limited = sorted.take(_maxMapMarkers).toList(growable: true);
    if (selectedId != null &&
        !limited.any((item) => item.cluster.representative.id == selectedId)) {
      final selected = items.where(
        (item) => item.cluster.representative.id == selectedId,
      );
      if (selected.isNotEmpty) {
        limited
          ..removeLast()
          ..add(selected.first);
      }
    }

    return _paintOrderedMarkerItems(limited);
  }

  List<_MapMarkerItem> _avoidOverlappingMarkerItems(
    List<_MapMarkerItem> items,
    MapCamera? camera,
  ) {
    if (items.length <= 1 || camera == null) {
      return items;
    }

    if (!camera.nonRotatedSize.width.isFinite ||
        !camera.nonRotatedSize.height.isFinite ||
        camera.nonRotatedSize.width <= 0 ||
        camera.nonRotatedSize.height <= 0) {
      return items;
    }

    final prioritized = List<_MapMarkerItem>.of(items)
      ..sort(
        (left, right) => _markerItemPriority(
          right,
          selectedOrganization?.id,
        ).compareTo(_markerItemPriority(left, selectedOrganization?.id)),
      );
    final selectedNeighborIds = _selectedNeighborIdsFor(items);
    final acceptedItems = <_MapMarkerItem>[];
    final acceptedRects = <Rect>[];

    for (final item in prioritized) {
      final rect = _screenRectForMarkerItem(item, camera);
      if (rect == null) {
        acceptedItems.add(item);
        continue;
      }

      final collides = acceptedRects.any(rect.overlaps);
      if (collides) {
        var fallbackAdded = false;
        final neighborPinItem = _neighborPinItemFor(item, selectedNeighborIds);
        if (neighborPinItem != null) {
          final neighborPinRect = _screenRectForMarkerItem(
            neighborPinItem,
            camera,
          );
          if (neighborPinRect == null ||
              !acceptedRects.any(neighborPinRect.overlaps)) {
            acceptedItems.add(neighborPinItem);
            fallbackAdded = true;
            if (neighborPinRect != null) {
              acceptedRects.add(
                neighborPinRect.inflate(_mapMarkerCollisionGap),
              );
            }
          }
        }
        if (!fallbackAdded) {
          final pinOnlyItem = _pinOnlyItemFor(item);
          if (pinOnlyItem != null) {
            acceptedItems.add(pinOnlyItem);
          }
        }
        continue;
      }

      acceptedItems.add(item);
      acceptedRects.add(rect.inflate(_mapMarkerCollisionGap));
    }

    return _paintOrderedMarkerItems(acceptedItems);
  }

  Set<String> _selectedNeighborIdsFor(List<_MapMarkerItem> items) {
    final selected = selectedOrganization;
    final selectedPosition = selected == null
        ? null
        : _validPositionFor(selected);
    if (selected == null || selectedPosition == null) {
      return const {};
    }

    final neighbors = <MapEntry<String, double>>[];
    for (final item in items) {
      if (!item.cluster.isSingle ||
          item.cluster.representative.id == selected.id) {
        continue;
      }

      final position = _validPositionFor(item.cluster.representative);
      if (position == null) {
        continue;
      }

      final distance = _radiusDistance(selectedPosition, position);
      if (distance <= radiusM) {
        neighbors.add(MapEntry(item.cluster.representative.id, distance));
      }
    }

    neighbors.sort((left, right) => left.value.compareTo(right.value));
    return neighbors
        .take(_maxSelectedNeighborPins)
        .map((entry) => entry.key)
        .toSet();
  }

  _MapMarkerItem? _pinOnlyItemFor(_MapMarkerItem item) {
    if (!item.cluster.isSingle ||
        item.selected ||
        item.mode == _MapMarkerMode.pinOnly) {
      return null;
    }

    return _MapMarkerItem(
      cluster: item.cluster,
      mode: _MapMarkerMode.pinOnly,
      selected: false,
      hovered: item.hovered,
      rank: item.rank,
    );
  }

  _MapMarkerItem? _neighborPinItemFor(
    _MapMarkerItem item,
    Set<String> selectedNeighborIds,
  ) {
    if (!item.cluster.isSingle ||
        item.selected ||
        !selectedNeighborIds.contains(item.cluster.representative.id)) {
      return null;
    }

    return _MapMarkerItem(
      cluster: item.cluster,
      mode: _MapMarkerMode.neighborPin,
      selected: false,
      hovered: item.hovered,
      rank: item.rank,
    );
  }

  Rect? _screenRectForMarkerItem(_MapMarkerItem item, MapCamera camera) {
    final size = _markerSizeFor(item);
    final point =
        camera.projectAtZoom(item.cluster.center) - camera.pixelOrigin;
    if (!point.dx.isFinite || !point.dy.isFinite) {
      return null;
    }

    if (item.mode != _MapMarkerMode.cluster && item.cluster.isSingle) {
      return Rect.fromLTWH(
        point.dx - size.width / 2,
        point.dy - size.height,
        size.width,
        size.height,
      );
    }

    return Rect.fromCenter(
      center: point,
      width: size.width,
      height: size.height,
    );
  }

  List<_MapMarkerItem> _paintOrderedMarkerItems(List<_MapMarkerItem> items) {
    return List<_MapMarkerItem>.of(items)..sort((left, right) {
      final leftOrder = _markerPaintOrder(left);
      final rightOrder = _markerPaintOrder(right);
      if (leftOrder != rightOrder) {
        return leftOrder.compareTo(rightOrder);
      }
      return left.cluster.count.compareTo(right.cluster.count);
    });
  }

  int _markerPaintOrder(_MapMarkerItem item) {
    if (item.mode == _MapMarkerMode.pinOnly) {
      return 5;
    }
    if (item.selected) {
      return 4;
    }
    if (item.mode == _MapMarkerMode.neighborPin) {
      return 3;
    }

    return switch (item.mode) {
      _MapMarkerMode.cluster => 0,
      _MapMarkerMode.compact => 1,
      _MapMarkerMode.detailed => 2,
      _MapMarkerMode.pinOnly => 5,
      _MapMarkerMode.neighborPin => 3,
    };
  }

  double _markerItemPriority(_MapMarkerItem item, String? selectedId) {
    if (item.cluster.representative.id == selectedId) {
      return 1000000;
    }
    if (item.hovered) {
      return 900000;
    }
    if (item.rank != null) {
      return 800000 - item.rank!.toDouble();
    }
    if (!item.cluster.isSingle) {
      return 10000.0 + item.cluster.count;
    }
    return _markerRepresentativeScore(item.cluster.representative);
  }

  double _clusterCircleRadius(int count) {
    if (count >= 100) {
      return 24;
    }
    if (count >= 25) {
      return 21;
    }
    if (count >= 10) {
      return 18;
    }
    return 16;
  }

  Color _clusterColor(int count) {
    if (count >= 100) {
      return const Color(0xFF0369A1);
    }
    if (count >= 25) {
      return const Color(0xFF0284C7);
    }
    return _selectedSkyBorder;
  }

  TextStyle _markerTextStyle({
    required double fontSize,
    required FontWeight fontWeight,
    Color color = _deepSkyText,
  }) {
    return TextStyle(
      color: color,
      fontSize: fontSize,
      fontWeight: fontWeight,
      height: 1.1,
    );
  }

  Widget _anchoredOrganizationMarker({
    required Widget child,
    required bool selected,
  }) {
    final anchorColor = selected
        ? _selectedRadiusMarkerBorder
        : _selectedSkyBorder;

    return Align(
      alignment: Alignment.bottomCenter,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          child,
          Transform.translate(
            offset: const Offset(0, -3),
            child: Icon(
              Icons.location_on_rounded,
              size: 24,
              color: anchorColor,
              shadows: const [
                Shadow(
                  color: Color(0x66000000),
                  blurRadius: 5,
                  offset: Offset(0, 2),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _markerTapTarget({
    required VoidCallback onTap,
    required Widget child,
    String? tooltip,
    String? hoverOrganizationId,
    ValueChanged<String?>? onHoverChanged,
  }) {
    return Tooltip(
      message: tooltip ?? '',
      waitDuration: const Duration(milliseconds: 450),
      child: MouseRegion(
        onEnter: hoverOrganizationId == null
            ? null
            : (_) => onHoverChanged?.call(hoverOrganizationId),
        onExit: hoverOrganizationId == null
            ? null
            : (_) => onHoverChanged?.call(null),
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: onTap,
          child: AnimatedScale(
            scale: 1,
            duration: const Duration(milliseconds: 180),
            curve: Curves.easeOutCubic,
            child: AnimatedOpacity(
              opacity: 1,
              duration: const Duration(milliseconds: 180),
              curve: Curves.easeOut,
              child: child,
            ),
          ),
        ),
      ),
    );
  }

  Widget _pinOnlyMarkerContent({
    required bool selected,
    required bool hovered,
  }) {
    final highlighted = selected || hovered;
    final color = selected ? _selectedRadiusMarkerBorder : _selectedSkyBorder;

    return Align(
      alignment: Alignment.bottomCenter,
      child: Icon(
        Icons.location_on_rounded,
        size: highlighted ? 30 : 26,
        color: color,
        shadows: const [
          Shadow(color: Color(0x66000000), blurRadius: 5, offset: Offset(0, 2)),
        ],
      ),
    );
  }

  Widget _clusterMarkerContent(int count) {
    final radius = _clusterCircleRadius(count);
    return Center(
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        curve: Curves.easeOutCubic,
        width: radius * 2,
        height: radius * 2,
        decoration: BoxDecoration(
          color: _clusterColor(count),
          shape: BoxShape.circle,
          border: Border.all(color: Colors.white, width: 3),
          boxShadow: [
            BoxShadow(
              color: _clusterColor(count).withValues(alpha: 0.28),
              blurRadius: 16,
              offset: const Offset(0, 6),
            ),
          ],
        ),
        child: Center(
          child: Text(
            count > 999 ? '999+' : count.toString(),
            style: const TextStyle(
              color: Colors.white,
              fontSize: 12,
              fontWeight: FontWeight.w800,
              height: 1,
            ),
          ),
        ),
      ),
    );
  }

  Widget _neighborPinMarkerContent(
    Organization organization,
    bool hovered,
    int? rank,
    double width,
  ) {
    final card = AnimatedContainer(
      duration: const Duration(milliseconds: 180),
      curve: Curves.easeOutCubic,
      width: width,
      height: hovered ? 40 : 36,
      padding: const EdgeInsets.symmetric(horizontal: 7),
      decoration: BoxDecoration(
        color: hovered ? _selectedSky : Colors.white.withAlpha(238),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(
          color: hovered ? _selectedSkyBorder : _fadedSkyBorder,
          width: hovered ? 2 : 1,
        ),
        boxShadow: [
          BoxShadow(
            color: _selectedSkyBorder.withValues(alpha: hovered ? 0.24 : 0.16),
            blurRadius: hovered ? 14 : 10,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Row(
        children: [
          if (rank != null) ...[
            _SmallMarkerBadge(text: '#$rank'),
            const SizedBox(width: 6),
          ],
          Expanded(
            child: Text(
              organization.displayTitle,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: _markerTextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w800,
              ),
            ),
          ),
        ],
      ),
    );

    return _anchoredOrganizationMarker(child: card, selected: false);
  }

  Widget _compactMarkerContent(
    Organization organization,
    int? count,
    bool selected,
    bool hovered,
    int? rank,
    double width,
  ) {
    final highlighted = selected || hovered;
    final borderColor = selected
        ? _selectedRadiusMarkerBorder
        : hovered
        ? _selectedSkyBorder
        : _fadedSkyBorder;
    final fillColor = selected
        ? _selectedRadiusMarker
        : hovered
        ? _fadedSky
        : Colors.white.withAlpha(232);
    final textColor = selected ? _selectedRadiusMarkerText : _deepSkyText;
    final showKpis = highlighted && count == null;
    final kpiBadges = showKpis
        ? _mapMarkerKpiBadgesFor(organization)
        : const <Widget>[];

    final card = AnimatedContainer(
      duration: const Duration(milliseconds: 180),
      curve: Curves.easeOutCubic,
      width: width,
      height: 42,
      padding: const EdgeInsets.symmetric(horizontal: 8),
      decoration: BoxDecoration(
        color: fillColor,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: borderColor, width: highlighted ? 2 : 1),
        boxShadow: [
          BoxShadow(
            color: borderColor.withValues(alpha: highlighted ? 0.22 : 0.12),
            blurRadius: highlighted ? 16 : 10,
            offset: const Offset(0, 5),
          ),
        ],
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (rank != null) ...[
            _SmallMarkerBadge(text: '#$rank'),
            const SizedBox(width: 6),
          ],
          Flexible(
            child: Text(
              organization.displayTitle,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: _markerTextStyle(
                fontSize: 11.5,
                fontWeight: selected ? FontWeight.w800 : FontWeight.w700,
                color: textColor,
              ),
            ),
          ),
          if (kpiBadges.isNotEmpty) ...[
            const SizedBox(width: 7),
            for (var i = 0; i < kpiBadges.length; i++) ...[
              if (i > 0) const SizedBox(width: 5),
              kpiBadges[i],
            ],
          ],
          if (count != null) ...[
            const SizedBox(width: 6),
            _SmallMarkerBadge(text: count.toString()),
          ],
        ],
      ),
    );

    if (count == null) {
      return _anchoredOrganizationMarker(child: card, selected: selected);
    }

    return Center(child: card);
  }

  Widget _detailedMarkerContent(
    Organization organization,
    bool selected,
    bool hovered,
    int? rank,
    double width,
  ) {
    final highlighted = selected || hovered;
    final borderColor = selected
        ? _selectedRadiusMarkerBorder
        : hovered
        ? _selectedSkyBorder
        : _fadedSkyBorder;
    final fillColor = selected
        ? _selectedRadiusMarker
        : hovered
        ? _fadedSky
        : Colors.white.withAlpha(238);
    final textColor = selected ? _selectedRadiusMarkerText : _deepSkyText;
    final kpiBadges = _mapMarkerKpiBadgesFor(organization);
    final card = AnimatedContainer(
      duration: const Duration(milliseconds: 200),
      curve: Curves.easeOutCubic,
      width: width,
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: fillColor,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: borderColor, width: highlighted ? 2 : 1),
        boxShadow: [
          BoxShadow(
            color: borderColor.withValues(alpha: highlighted ? 0.24 : 0.14),
            blurRadius: highlighted ? 18 : 12,
            offset: const Offset(0, 7),
          ),
        ],
      ),
      child: Row(
        children: [
          if (rank != null) ...[
            _SmallMarkerBadge(text: '#$rank'),
            const SizedBox(width: 7),
          ],
          Flexible(
            child: Text(
              organization.displayTitle,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: _markerTextStyle(
                fontSize: 11.5,
                fontWeight: FontWeight.w800,
                color: textColor,
              ),
            ),
          ),
          if (kpiBadges.isNotEmpty) ...[
            const SizedBox(width: 5),
            for (var i = 0; i < kpiBadges.length; i++) ...[
              if (i > 0) const SizedBox(width: 4),
              kpiBadges[i],
            ],
          ],
        ],
      ),
    );

    return _anchoredOrganizationMarker(child: card, selected: selected);
  }

  List<Organization> _organizationsInViewport() {
    if (organizations.isEmpty) {
      return organizations;
    }

    final LatLngBounds visibleBounds;
    try {
      visibleBounds = mapController.camera.visibleBounds;
    } on Object {
      return organizations;
    }

    final paddedBounds = _paddedViewportBounds(visibleBounds);
    return organizations
        .where((organization) => paddedBounds.contains(organization.position))
        .toList(growable: false);
  }

  LatLngBounds _paddedViewportBounds(LatLngBounds bounds) {
    final latPadding = (bounds.north - bounds.south).abs() * 0.35;
    final lonPadding = (bounds.east - bounds.west).abs() * 0.35;

    return LatLngBounds.unsafe(
      north: math.min(_mapBounds.north, bounds.north + latPadding),
      south: math.max(_mapBounds.south, bounds.south - latPadding),
      east: math.min(_mapBounds.east, bounds.east + lonPadding),
      west: math.max(_mapBounds.west, bounds.west - lonPadding),
    );
  }

  List<_MapCluster> _clustersForZoom(List<Organization> organizations) {
    if (organizations.isEmpty) {
      return const [];
    }

    final zoom = currentZoom ?? _initialZoomFor(organizations);
    if (zoom >= 17) {
      return organizations
          .map(
            (organization) => _MapCluster(
              organizations: [organization],
              center: organization.position,
              representative: organization,
              key: 'org:${organization.id}',
            ),
          )
          .toList(growable: false);
    }

    final cellSize = _cellSizeForZoom(zoom);
    final selectedId = selectedOrganization?.id;
    final selectedNeighborIds = _selectedNeighborIdsForOrganizations(
      organizations,
    );
    final byCell = <String, List<Organization>>{};

    for (final organization in organizations) {
      if (organization.id == selectedId ||
          selectedNeighborIds.contains(organization.id)) {
        continue;
      }

      final key = _gridKeyFor(organization, cellSize);
      byCell.putIfAbsent(key, () => <Organization>[]).add(organization);
    }

    final clusters = byCell.entries
        .map((entry) => _clusterFrom(entry.key, entry.value))
        .toList(growable: true);
    final selected = selectedOrganization;
    if (selected != null) {
      clusters.add(
        _MapCluster(
          organizations: [selected],
          center: selected.position,
          representative: selected,
          key: 'selected:${selected.id}',
        ),
      );
    }
    for (final organization in organizations) {
      if (!selectedNeighborIds.contains(organization.id)) {
        continue;
      }

      clusters.add(
        _MapCluster(
          organizations: [organization],
          center: organization.position,
          representative: organization,
          key: 'selected-neighbor:${organization.id}',
        ),
      );
    }
    return clusters;
  }

  Set<String> _selectedNeighborIdsForOrganizations(
    List<Organization> organizations,
  ) {
    final selected = selectedOrganization;
    final selectedPosition = selected == null
        ? null
        : _validPositionFor(selected);
    if (selected == null || selectedPosition == null) {
      return const {};
    }

    final neighbors = <MapEntry<String, double>>[];
    for (final organization in organizations) {
      if (organization.id == selected.id) {
        continue;
      }

      final position = _validPositionFor(organization);
      if (position == null) {
        continue;
      }

      final distance = _radiusDistance(selectedPosition, position);
      if (distance <= radiusM) {
        neighbors.add(MapEntry(organization.id, distance));
      }
    }

    neighbors.sort((left, right) => left.value.compareTo(right.value));
    return neighbors
        .take(_maxSelectedNeighborPins)
        .map((entry) => entry.key)
        .toSet();
  }

  _MapCluster _clusterFrom(String key, List<Organization> organizations) {
    var latSum = 0.0;
    var lonSum = 0.0;
    var representative = organizations.first;
    var representativeScore = _markerRepresentativeScore(representative);

    for (final organization in organizations) {
      latSum += organization.lat;
      lonSum += organization.lon;

      final score = _markerRepresentativeScore(organization);
      if (score > representativeScore) {
        representative = organization;
        representativeScore = score;
      }
    }

    return _MapCluster(
      organizations: List<Organization>.unmodifiable(organizations),
      center: LatLng(
        latSum / organizations.length,
        lonSum / organizations.length,
      ),
      representative: representative,
      key: key,
    );
  }

  String _gridKeyFor(Organization organization, double cellSize) {
    final latKey = (organization.lat / cellSize).floor();
    final lonKey = (organization.lon / cellSize).floor();
    return '$latKey:$lonKey';
  }

  double _cellSizeForZoom(double zoom) {
    if (zoom >= 16) {
      return 0.0018;
    }
    if (zoom >= 14) {
      return 0.0035;
    }
    if (zoom >= 13) {
      return 0.005;
    }
    if (zoom >= 12) {
      return 0.007;
    }
    if (zoom >= 11) {
      return 0.014;
    }
    if (zoom >= 10) {
      return 0.028;
    }
    if (zoom >= 9) {
      return 0.055;
    }
    return 0.11;
  }

  double _markerRepresentativeScore(Organization organization) {
    final rating =
        double.tryParse(organization.ratingValue.replaceAll(',', '.')) ?? 0;
    final reviewScore = math.log((organization.ratingCount ?? 0) + 1);
    return rating * 10 + reviewScore;
  }

  List<Widget> _mapMarkerKpiBadgesFor(Organization organization) {
    final badges = <Widget>[];
    if (organization.ratingValue.isNotEmpty) {
      badges.add(
        _MapMarkerKpiBadge(
          text: organization.ratingValue,
          tooltip: 'Общий рейтинг организации',
        ),
      );
    }

    final ratingCount = organization.ratingCount;
    if (ratingCount != null && ratingCount > 0) {
      badges.add(
        _MapMarkerKpiBadge(
          text: ratingCount.toString(),
          tooltip: 'Количество отзывов организации',
        ),
      );
    }

    return badges;
  }

  List<String> _mapMarkerKpiTextsFor(Organization organization) {
    return [
      if (organization.ratingValue.isNotEmpty) organization.ratingValue,
      if (organization.ratingCount != null && organization.ratingCount! > 0)
        organization.ratingCount!.toString(),
    ];
  }

  LatLng _centerFor(List<Organization> organizations) {
    var minLat = organizations.first.lat;
    var maxLat = organizations.first.lat;
    var minLon = organizations.first.lon;
    var maxLon = organizations.first.lon;

    for (final organization in organizations.skip(1)) {
      minLat = math.min(minLat, organization.lat);
      maxLat = math.max(maxLat, organization.lat);
      minLon = math.min(minLon, organization.lon);
      maxLon = math.max(maxLon, organization.lon);
    }

    return LatLng((minLat + maxLat) / 2, (minLon + maxLon) / 2);
  }

  double _initialZoomFor(List<Organization> organizations) {
    if (organizations.length == 1) {
      return 15;
    }

    final lats = organizations.map((organization) => organization.lat);
    final lons = organizations.map((organization) => organization.lon);
    final latSpread = lats.reduce(math.max) - lats.reduce(math.min);
    final lonSpread = lons.reduce(math.max) - lons.reduce(math.min);
    final spread = math.max(latSpread, lonSpread);

    if (spread < 0.02) {
      return 14;
    }
    if (spread < 0.08) {
      return 12.5;
    }
    if (spread < 0.25) {
      return 11;
    }
    return 9.5;
  }
}

class _SmallMarkerBadge extends StatelessWidget {
  const _SmallMarkerBadge({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: _fadedSky,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: _fadedSkyBorder),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
        child: Text(
          text,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(
            color: _deepSkyText,
            fontSize: 10.5,
            fontWeight: FontWeight.w800,
            height: 1,
          ),
        ),
      ),
    );
  }
}

class _MapMarkerKpiBadge extends StatelessWidget {
  const _MapMarkerKpiBadge({required this.text, required this.tooltip});

  final String text;
  final String tooltip;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: _fadedSky,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: _fadedSkyBorder),
        ),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 3),
          child: Text(
            text,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
              color: _deepSkyText,
              fontSize: 9.5,
              fontWeight: FontWeight.w800,
              height: 1,
            ),
          ),
        ),
      ),
    );
  }
}

class _RadiusControlCard extends StatefulWidget {
  const _RadiusControlCard({
    required this.radiusM,
    required this.count,
    required this.nearestDistanceM,
    required this.radiusOnly,
    required this.onRadiusChanged,
    required this.onRadiusOnlyChanged,
  });

  final int radiusM;
  final int count;
  final double? nearestDistanceM;
  final bool radiusOnly;
  final ValueChanged<int> onRadiusChanged;
  final ValueChanged<bool> onRadiusOnlyChanged;

  @override
  State<_RadiusControlCard> createState() => _RadiusControlCardState();
}

class _RadiusControlCardState extends State<_RadiusControlCard> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final normalizedRadiusM = _snapRadiusM(widget.radiusM);
    final selectedPreset = _radiusPresetOptions.contains(normalizedRadiusM)
        ? <int>{normalizedRadiusM}
        : <int>{};
    final nearestDistance = widget.nearestDistanceM;
    final summary = nearestDistance == null
        ? '${widget.count} рядом'
        : '${widget.count} рядом · ближайшая ${_distanceLabelFor(nearestDistance)}';

    return AnimatedContainer(
      duration: const Duration(milliseconds: 180),
      curve: Curves.easeOut,
      width: 300,
      child: Material(
        color: colorScheme.surface.withValues(alpha: 0.94),
        elevation: 4,
        borderRadius: BorderRadius.circular(8),
        clipBehavior: Clip.antiAlias,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            InkWell(
              onTap: () => setState(() => _expanded = !_expanded),
              child: Padding(
                padding: const EdgeInsets.fromLTRB(10, 8, 8, 8),
                child: Row(
                  children: [
                    const Icon(Icons.radio_button_unchecked_rounded, size: 18),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        'Радиус ${_radiusLabelFor(normalizedRadiusM)}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: Theme.of(context).textTheme.labelLarge?.copyWith(
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Flexible(
                      child: Text(
                        summary,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        textAlign: TextAlign.end,
                        style: Theme.of(context).textTheme.labelSmall?.copyWith(
                          color: colorScheme.onSurfaceVariant,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ),
                    const SizedBox(width: 6),
                    AnimatedRotation(
                      turns: _expanded ? 0.5 : 0,
                      duration: const Duration(milliseconds: 180),
                      curve: Curves.easeOut,
                      child: const Icon(Icons.expand_more_rounded, size: 20),
                    ),
                  ],
                ),
              ),
            ),
            AnimatedSwitcher(
              duration: const Duration(milliseconds: 180),
              switchInCurve: Curves.easeOut,
              switchOutCurve: Curves.easeIn,
              child: _expanded
                  ? Padding(
                      key: const ValueKey('radius-selector-expanded'),
                      padding: const EdgeInsets.fromLTRB(10, 0, 10, 10),
                      child: _RadiusSelectorPanel(
                        normalizedRadiusM: normalizedRadiusM,
                        selectedPreset: selectedPreset,
                        onChanged: widget.onRadiusChanged,
                      ),
                    )
                  : const SizedBox.shrink(
                      key: ValueKey('radius-selector-collapsed'),
                    ),
            ),
            if (_expanded) ...[
              const Divider(height: 1),
              _MapScopeToggle(
                radiusOnly: widget.radiusOnly,
                onChanged: widget.onRadiusOnlyChanged,
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _RadiusSelectorPanel extends StatelessWidget {
  const _RadiusSelectorPanel({
    required this.normalizedRadiusM,
    required this.selectedPreset,
    required this.onChanged,
  });

  final int normalizedRadiusM;
  final Set<int> selectedPreset;
  final ValueChanged<int> onChanged;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Slider(
          value: normalizedRadiusM.toDouble(),
          min: _minRadiusM.toDouble(),
          max: _maxRadiusM.toDouble(),
          divisions: (_maxRadiusM - _minRadiusM) ~/ _radiusStepM,
          label: _radiusLabelFor(normalizedRadiusM),
          onChanged: (value) => onChanged(_snapRadiusM(value.round())),
        ),
        SegmentedButton<int>(
          segments: _radiusPresetOptions
              .map(
                (radiusM) => ButtonSegment<int>(
                  value: radiusM,
                  label: Text(_radiusLabelFor(radiusM)),
                ),
              )
              .toList(growable: false),
          selected: selectedPreset,
          emptySelectionAllowed: true,
          showSelectedIcon: false,
          style: const ButtonStyle(
            tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            visualDensity: VisualDensity.compact,
          ),
          onSelectionChanged: (selection) {
            if (selection.isEmpty) {
              return;
            }
            onChanged(selection.first);
          },
        ),
      ],
    );
  }
}

class _MapScopeToggle extends StatelessWidget {
  const _MapScopeToggle({required this.radiusOnly, required this.onChanged});

  final bool radiusOnly;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    return InkWell(
      onTap: () => onChanged(!radiusOnly),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(10, 7, 6, 7),
        child: Row(
          children: [
            Icon(
              radiusOnly ? Icons.radar_rounded : Icons.map_rounded,
              size: 17,
              color: colorScheme.primary,
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                radiusOnly ? 'На карте: радиус' : 'На карте: все',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.labelMedium?.copyWith(
                  color: colorScheme.onSurface,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
            Switch(
              value: radiusOnly,
              onChanged: onChanged,
              materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
          ],
        ),
      ),
    );
  }
}

class _ViewportLoadingBadge extends StatelessWidget {
  const _ViewportLoadingBadge();

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    return Material(
      color: colorScheme.surface.withValues(alpha: 0.94),
      elevation: 4,
      borderRadius: BorderRadius.circular(8),
      clipBehavior: Clip.antiAlias,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            SizedBox.square(
              dimension: 16,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                color: colorScheme.primary,
              ),
            ),
            const SizedBox(width: 9),
            Text(
              'Загружаю область...',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(context).textTheme.labelMedium?.copyWith(
                color: colorScheme.onSurface,
                fontWeight: FontWeight.w700,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _RadiusEmptyState extends StatelessWidget {
  const _RadiusEmptyState({
    required this.radiusM,
    required this.onRadiusSelected,
  });

  final int radiusM;
  final ValueChanged<int> onRadiusSelected;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final nextRadiusOptions = _radiusPresetOptions
        .where((option) => option > _snapRadiusM(radiusM))
        .toList(growable: false);

    return DecoratedBox(
      decoration: BoxDecoration(
        color: _fadedSky,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: _fadedSkyBorder),
      ),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.radar_rounded, size: 20, color: colorScheme.primary),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Рядом ничего не найдено',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: Theme.of(context).textTheme.titleSmall?.copyWith(
                          color: _deepSkyText,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                      const SizedBox(height: 3),
                      Text(
                        'В радиусе ${_radiusLabelFor(radiusM)} нет других организаций.',
                        style: Theme.of(context).textTheme.bodySmall?.copyWith(
                          color: _deepSkyText.withValues(alpha: 0.76),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            if (nextRadiusOptions.isNotEmpty) ...[
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: nextRadiusOptions
                    .map(
                      (option) => FilledButton.tonal(
                        onPressed: () => onRadiusSelected(option),
                        child: Text('До ${_radiusLabelFor(option)}'),
                      ),
                    )
                    .toList(growable: false),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _MapControls extends StatelessWidget {
  const _MapControls({
    required this.hasSelectedOrganization,
    required this.onZoomIn,
    required this.onZoomOut,
    required this.onFocusSelected,
  });

  final bool hasSelectedOrganization;
  final VoidCallback onZoomIn;
  final VoidCallback onZoomOut;
  final VoidCallback onFocusSelected;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    return Material(
      color: colorScheme.surface.withValues(alpha: 0.94),
      elevation: 4,
      borderRadius: BorderRadius.circular(8),
      clipBehavior: Clip.antiAlias,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          _MapControlButton(
            tooltip: 'Приблизить',
            icon: Icons.add_rounded,
            onPressed: onZoomIn,
          ),
          const Divider(height: 1),
          _MapControlButton(
            tooltip: 'Отдалить',
            icon: Icons.remove_rounded,
            onPressed: onZoomOut,
          ),
          const Divider(height: 1),
          _MapControlButton(
            tooltip: 'К выбранной организации',
            icon: Icons.my_location_rounded,
            onPressed: hasSelectedOrganization ? onFocusSelected : null,
          ),
        ],
      ),
    );
  }
}

class _MapControlButton extends StatelessWidget {
  const _MapControlButton({
    required this.tooltip,
    required this.icon,
    required this.onPressed,
  });

  final String tooltip;
  final IconData icon;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return SizedBox.square(
      dimension: 42,
      child: IconButton(
        tooltip: tooltip,
        onPressed: onPressed,
        icon: Icon(icon),
      ),
    );
  }
}

class _DesktopPanel extends StatelessWidget {
  const _DesktopPanel({
    required this.organizations,
    required this.selectedOrganization,
    required this.selectedDetailsExpanded,
    required this.reviewsAnalysisLoading,
    required this.radiusReviewsAnalysisLoading,
    required this.selectedReviewDynamics,
    required this.selectedReviewDynamicsLoading,
    required this.selectedReviewDynamicsError,
    required this.radiusM,
    required this.hasRadiusResults,
    required this.hoveredOrganizationId,
    required this.nearbyCount,
    required this.nearestDistanceM,
    required this.nearestRanksById,
    required this.onSelected,
    required this.onHoverChanged,
    required this.onToggleSelectedDetails,
    required this.onClearSelected,
    required this.onRadiusSelected,
    required this.onAnalyzeReviews,
    required this.onAnalyzeRadiusReviews,
    required this.onRefreshReviewDynamics,
  });

  final List<Organization> organizations;
  final Organization? selectedOrganization;
  final bool selectedDetailsExpanded;
  final bool reviewsAnalysisLoading;
  final bool radiusReviewsAnalysisLoading;
  final ReviewDynamics? selectedReviewDynamics;
  final bool selectedReviewDynamicsLoading;
  final Object? selectedReviewDynamicsError;
  final int radiusM;
  final bool hasRadiusResults;
  final String? hoveredOrganizationId;
  final int nearbyCount;
  final double? nearestDistanceM;
  final Map<String, int> nearestRanksById;
  final ValueChanged<Organization> onSelected;
  final ValueChanged<String?> onHoverChanged;
  final VoidCallback onToggleSelectedDetails;
  final VoidCallback onClearSelected;
  final ValueChanged<int> onRadiusSelected;
  final VoidCallback onAnalyzeReviews;
  final VoidCallback onAnalyzeRadiusReviews;
  final VoidCallback? onRefreshReviewDynamics;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    return Material(
      color: colorScheme.surface,
      elevation: 2,
      child: LayoutBuilder(
        builder: (context, constraints) {
          final selectedCardMaxHeight = constraints.maxHeight.isFinite
              ? math.max(0.0, math.min(620.0, constraints.maxHeight * 0.64))
              : 620.0;

          return Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 14, 16, 12),
                child: _SummaryHeader(count: organizations.length),
              ),
              if (selectedOrganization != null)
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
                  child: ConstrainedBox(
                    constraints: BoxConstraints(
                      maxHeight: selectedCardMaxHeight,
                    ),
                    child: _SelectedOrganizationCard(
                      organization: selectedOrganization!,
                      expanded: selectedDetailsExpanded,
                      nearbyCount: nearbyCount,
                      nearestDistanceM: nearestDistanceM,
                      reviewsAnalysisLoading: reviewsAnalysisLoading,
                      radiusReviewsAnalysisLoading:
                          radiusReviewsAnalysisLoading,
                      radiusM: radiusM,
                      reviewDynamics: selectedReviewDynamics,
                      reviewDynamicsLoading: selectedReviewDynamicsLoading,
                      reviewDynamicsError: selectedReviewDynamicsError,
                      onToggle: onToggleSelectedDetails,
                      onClose: onClearSelected,
                      onAnalyzeReviews: onAnalyzeReviews,
                      onAnalyzeRadiusReviews: onAnalyzeRadiusReviews,
                      onRefreshReviewDynamics: onRefreshReviewDynamics,
                    ),
                  ),
                ),
              const Divider(height: 1),
              Expanded(
                child: !hasRadiusResults && selectedOrganization != null
                    ? _RadiusEmptyState(
                        radiusM: radiusM,
                        onRadiusSelected: onRadiusSelected,
                      )
                    : _OrganizationsList(
                        organizations: organizations,
                        selectedOrganization: selectedOrganization,
                        hoveredOrganizationId: hoveredOrganizationId,
                        nearestRanksById: nearestRanksById,
                        onSelected: onSelected,
                        onHoverChanged: onHoverChanged,
                      ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class _CompactPanel extends StatelessWidget {
  const _CompactPanel({
    required this.count,
    required this.selectedOrganization,
    required this.selectedDetailsExpanded,
    required this.reviewsAnalysisLoading,
    required this.radiusReviewsAnalysisLoading,
    required this.selectedReviewDynamics,
    required this.selectedReviewDynamicsLoading,
    required this.selectedReviewDynamicsError,
    required this.radiusM,
    required this.hasRadiusResults,
    required this.nearbyCount,
    required this.nearestDistanceM,
    required this.onToggleSelectedDetails,
    required this.onClearSelected,
    required this.onRadiusSelected,
    required this.onAnalyzeReviews,
    required this.onAnalyzeRadiusReviews,
    required this.onRefreshReviewDynamics,
    required this.onOpenList,
  });

  final int count;
  final Organization? selectedOrganization;
  final bool selectedDetailsExpanded;
  final bool reviewsAnalysisLoading;
  final bool radiusReviewsAnalysisLoading;
  final ReviewDynamics? selectedReviewDynamics;
  final bool selectedReviewDynamicsLoading;
  final Object? selectedReviewDynamicsError;
  final int radiusM;
  final bool hasRadiusResults;
  final int nearbyCount;
  final double? nearestDistanceM;
  final VoidCallback onToggleSelectedDetails;
  final VoidCallback onClearSelected;
  final ValueChanged<int> onRadiusSelected;
  final VoidCallback onAnalyzeReviews;
  final VoidCallback onAnalyzeRadiusReviews;
  final VoidCallback? onRefreshReviewDynamics;
  final VoidCallback onOpenList;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    return Material(
      color: colorScheme.surface,
      elevation: 8,
      borderRadius: BorderRadius.circular(8),
      clipBehavior: Clip.antiAlias,
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(child: _SummaryHeader(count: count)),
                IconButton.filledTonal(
                  tooltip: 'Список',
                  onPressed: onOpenList,
                  icon: const Icon(Icons.list_alt_rounded),
                ),
              ],
            ),
            if (selectedOrganization != null) ...[
              const SizedBox(height: 10),
              _SelectedOrganizationCard(
                organization: selectedOrganization!,
                expanded: selectedDetailsExpanded,
                nearbyCount: nearbyCount,
                nearestDistanceM: nearestDistanceM,
                reviewsAnalysisLoading: reviewsAnalysisLoading,
                radiusReviewsAnalysisLoading: radiusReviewsAnalysisLoading,
                radiusM: radiusM,
                reviewDynamics: selectedReviewDynamics,
                reviewDynamicsLoading: selectedReviewDynamicsLoading,
                reviewDynamicsError: selectedReviewDynamicsError,
                onToggle: onToggleSelectedDetails,
                onClose: onClearSelected,
                onAnalyzeReviews: onAnalyzeReviews,
                onAnalyzeRadiusReviews: onAnalyzeRadiusReviews,
                onRefreshReviewDynamics: onRefreshReviewDynamics,
              ),
              if (!hasRadiusResults) ...[
                const SizedBox(height: 10),
                _RadiusEmptyState(
                  radiusM: radiusM,
                  onRadiusSelected: onRadiusSelected,
                ),
              ],
            ],
          ],
        ),
      ),
    );
  }
}

class _IconBadge extends StatelessWidget {
  const _IconBadge({required this.icon, this.selected = false});

  final IconData icon;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 180),
      width: 36,
      height: 36,
      decoration: BoxDecoration(
        color: selected ? _selectedSky : _fadedSky,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(
          color: selected ? _selectedSkyBorder : _fadedSkyBorder,
          width: selected ? 2 : 1,
        ),
      ),
      child: Icon(icon, size: 19, color: _deepSkyText),
    );
  }
}

class _SummaryHeader extends StatelessWidget {
  const _SummaryHeader({required this.count});

  final int count;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final colorScheme = Theme.of(context).colorScheme;

    return Row(
      children: [
        const _IconBadge(icon: Icons.map_rounded),
        const SizedBox(width: 10),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Организации',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: textTheme.titleMedium?.copyWith(
                  fontWeight: FontWeight.w700,
                ),
              ),
              Text(
                '$count организаций в списке',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: textTheme.bodySmall?.copyWith(
                  color: colorScheme.onSurfaceVariant,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _SelectedOrganizationCard extends StatelessWidget {
  const _SelectedOrganizationCard({
    required this.organization,
    required this.expanded,
    required this.nearbyCount,
    required this.nearestDistanceM,
    required this.reviewsAnalysisLoading,
    required this.radiusReviewsAnalysisLoading,
    required this.radiusM,
    required this.reviewDynamics,
    required this.reviewDynamicsLoading,
    required this.reviewDynamicsError,
    required this.onToggle,
    required this.onClose,
    required this.onAnalyzeReviews,
    required this.onAnalyzeRadiusReviews,
    required this.onRefreshReviewDynamics,
  });

  final Organization organization;
  final bool expanded;
  final int nearbyCount;
  final double? nearestDistanceM;
  final bool reviewsAnalysisLoading;
  final bool radiusReviewsAnalysisLoading;
  final int radiusM;
  final ReviewDynamics? reviewDynamics;
  final bool reviewDynamicsLoading;
  final Object? reviewDynamicsError;
  final VoidCallback onToggle;
  final VoidCallback onClose;
  final VoidCallback onAnalyzeReviews;
  final VoidCallback onAnalyzeRadiusReviews;
  final VoidCallback? onRefreshReviewDynamics;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;

    return AnimatedContainer(
      duration: const Duration(milliseconds: 180),
      curve: Curves.easeOut,
      decoration: BoxDecoration(
        color: _selectedSky,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: _selectedSkyBorder, width: 2),
        boxShadow: [
          BoxShadow(
            color: _selectedSkyBorder.withValues(alpha: 0.14),
            blurRadius: 12,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(10),
        child: AnimatedSize(
          duration: const Duration(milliseconds: 180),
          curve: Curves.easeOut,
          alignment: Alignment.topCenter,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Row(
                children: [
                  _IconBadge(
                    icon: _organizationIcon(organization),
                    selected: true,
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          organization.displayTitle,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: textTheme.titleSmall?.copyWith(
                            color: _deepSkyText,
                            fontWeight: FontWeight.w800,
                          ),
                        ),
                        Text(
                          _compactSubtitleFor(organization),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: textTheme.bodySmall?.copyWith(
                            color: _deepSkyText.withValues(alpha: 0.76),
                          ),
                        ),
                      ],
                    ),
                  ),
                  IconButton(
                    tooltip: expanded ? 'Свернуть детали' : 'Показать детали',
                    onPressed: onToggle,
                    icon: AnimatedRotation(
                      turns: expanded ? 0.5 : 0,
                      duration: const Duration(milliseconds: 180),
                      curve: Curves.easeOut,
                      child: const Icon(Icons.expand_more_rounded),
                    ),
                  ),
                  IconButton(
                    tooltip: 'Снять выбор',
                    onPressed: onClose,
                    icon: const Icon(Icons.close_rounded),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              _SelectedKpiRow(
                organization: organization,
                nearbyCount: nearbyCount,
                nearestDistanceM: nearestDistanceM,
              ),
              const SizedBox(height: 8),
              _ReviewDynamicsSummaryPanel(
                dynamics: reviewDynamics,
                loading: reviewDynamicsLoading,
                error: reviewDynamicsError,
                onRefresh: onRefreshReviewDynamics,
              ),
              AnimatedSwitcher(
                duration: const Duration(milliseconds: 180),
                switchInCurve: Curves.easeOut,
                switchOutCurve: Curves.easeIn,
                child: expanded
                    ? Padding(
                        key: const ValueKey('selected-details-expanded'),
                        padding: const EdgeInsets.only(top: 10),
                        child: _OrganizationDetails(
                          organization: organization,
                          reviewsAnalysisLoading: reviewsAnalysisLoading,
                          radiusReviewsAnalysisLoading:
                              radiusReviewsAnalysisLoading,
                          radiusM: radiusM,
                          onAnalyzeReviews: onAnalyzeReviews,
                          onAnalyzeRadiusReviews: onAnalyzeRadiusReviews,
                        ),
                      )
                    : const SizedBox.shrink(
                        key: ValueKey('selected-details-collapsed'),
                      ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  String _compactSubtitleFor(Organization organization) {
    final parts = <String>[
      if (organization.categoryLabel.isNotEmpty) organization.categoryLabel,
      if (organization.ratingValue.isNotEmpty) organization.ratingValue,
      if (organization.fullAddress.isNotEmpty) organization.fullAddress,
    ];

    if (parts.isEmpty) {
      return '${organization.lat.toStringAsFixed(5)}, '
          '${organization.lon.toStringAsFixed(5)}';
    }
    return parts.join(' | ');
  }
}

class _SelectedKpiRow extends StatelessWidget {
  const _SelectedKpiRow({
    required this.organization,
    required this.nearbyCount,
    required this.nearestDistanceM,
  });

  final Organization organization;
  final int nearbyCount;
  final double? nearestDistanceM;

  @override
  Widget build(BuildContext context) {
    final nearestDistance = nearestDistanceM;
    final chips = <Widget>[
      _SelectedKpiChip(icon: Icons.radar_rounded, label: '$nearbyCount рядом'),
      _SelectedKpiChip(
        icon: Icons.near_me_rounded,
        label: nearestDistance == null
            ? 'нет соседей'
            : _distanceLabelFor(nearestDistance),
      ),
      if (organization.ratingValue.isNotEmpty)
        _SelectedKpiChip(
          icon: Icons.star_rounded,
          label: organization.ratingValue,
        ),
      if (organization.ratingCount != null && organization.ratingCount! > 0)
        _SelectedKpiChip(
          icon: Icons.chat_bubble_rounded,
          label: '${organization.ratingCount}',
        ),
    ];

    return Align(
      alignment: Alignment.centerLeft,
      child: Wrap(spacing: 6, runSpacing: 6, children: chips),
    );
  }
}

class _SelectedKpiChip extends StatelessWidget {
  const _SelectedKpiChip({required this.icon, required this.label});

  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.58),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: _fadedSkyBorder),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 4),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 14, color: _deepSkyText),
            const SizedBox(width: 4),
            Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                color: _deepSkyText,
                fontSize: 11,
                fontWeight: FontWeight.w800,
                height: 1,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ReviewDynamicsSummaryPanel extends StatelessWidget {
  const _ReviewDynamicsSummaryPanel({
    required this.dynamics,
    required this.loading,
    required this.error,
    required this.onRefresh,
  });

  final ReviewDynamics? dynamics;
  final bool loading;
  final Object? error;
  final VoidCallback? onRefresh;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final currentDynamics = dynamics;

    return DecoratedBox(
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.46),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: _fadedSkyBorder),
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(10, 8, 8, 8),
        child: loading && currentDynamics == null
            ? _ReviewDynamicsMessage(
                icon: Icons.insights_rounded,
                title: 'Динамика отзывов',
                text: 'Загрузка',
                trailing: const SizedBox.square(
                  dimension: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              )
            : error != null && currentDynamics == null
            ? _ReviewDynamicsMessage(
                icon: Icons.error_outline_rounded,
                title: 'Динамика отзывов',
                text: 'Недоступна',
                trailing: _ReviewDynamicsRefreshButton(onRefresh: onRefresh),
              )
            : currentDynamics == null
            ? _ReviewDynamicsMessage(
                icon: Icons.insights_rounded,
                title: 'Динамика отзывов',
                text: 'Нет данных',
                trailing: _ReviewDynamicsRefreshButton(onRefresh: onRefresh),
              )
            : Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const Icon(
                        Icons.insights_rounded,
                        size: 16,
                        color: _deepSkyText,
                      ),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          'Динамика отзывов',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: textTheme.labelMedium?.copyWith(
                            color: _deepSkyText,
                            fontWeight: FontWeight.w800,
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      _ReviewDynamicsStatusPill(
                        status: _reviewDynamicsStatusFor(currentDynamics),
                        tooltip: _reviewDynamicsStatusTooltip(currentDynamics),
                      ),
                      if (loading) ...[
                        const SizedBox(width: 8),
                        const SizedBox.square(
                          dimension: 14,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        ),
                      ],
                    ],
                  ),
                  const SizedBox(height: 7),
                  Tooltip(
                    message: _reviewDynamicsThirtyDayTooltip(currentDynamics),
                    child: Text(
                      '30д: ${currentDynamics.reviewsLast30Days} · '
                      '${_reviewDynamicsGrowthText(currentDynamics.growth30dAbs)}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: textTheme.bodySmall?.copyWith(
                        color: _deepSkyText,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  const SizedBox(height: 7),
                  Wrap(
                    spacing: 6,
                    runSpacing: 6,
                    children: [
                      _ReviewDynamicsMetricChip(
                        icon: Icons.calendar_view_week_rounded,
                        label: '7д ${currentDynamics.reviewsLast7Days}',
                        tooltip: _reviewDynamicsPeriodTooltip(
                          days: 7,
                          current: currentDynamics.reviewsLast7Days,
                          previous: currentDynamics.reviewsPrevious7Days,
                          growth: currentDynamics.growth7dAbs,
                        ),
                      ),
                      _ReviewDynamicsMetricChip(
                        icon: Icons.date_range_rounded,
                        label: '90д ${currentDynamics.reviewsLast90Days}',
                        tooltip: _reviewDynamicsPeriodTooltip(
                          days: 90,
                          current: currentDynamics.reviewsLast90Days,
                          previous: currentDynamics.reviewsPrevious90Days,
                          growth: currentDynamics.growth90dAbs,
                        ),
                      ),
                      _ReviewDynamicsMetricChip(
                        icon: Icons.star_rounded,
                        label: _reviewDynamicsRatingText(
                          currentDynamics.currentRating,
                        ),
                        tooltip: _reviewDynamicsRatingTooltip(currentDynamics),
                      ),
                      _ReviewDynamicsMetricChip(
                        icon: Icons.warning_amber_rounded,
                        label: _reviewDynamicsNegativeText(currentDynamics),
                        danger: currentDynamics.hasNegativeRisk,
                        tooltip: _reviewDynamicsNegativeTooltip(
                          currentDynamics,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
      ),
    );
  }
}

class _ReviewDynamicsMessage extends StatelessWidget {
  const _ReviewDynamicsMessage({
    required this.icon,
    required this.title,
    required this.text,
    required this.trailing,
  });

  final IconData icon;
  final String title;
  final String text;
  final Widget trailing;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;

    return Row(
      children: [
        Icon(icon, size: 16, color: _deepSkyText),
        const SizedBox(width: 6),
        Expanded(
          child: Text(
            '$title: $text',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: textTheme.bodySmall?.copyWith(
              color: _deepSkyText,
              fontWeight: FontWeight.w700,
            ),
          ),
        ),
        const SizedBox(width: 8),
        trailing,
      ],
    );
  }
}

class _ReviewDynamicsRefreshButton extends StatelessWidget {
  const _ReviewDynamicsRefreshButton({required this.onRefresh});

  final VoidCallback? onRefresh;

  @override
  Widget build(BuildContext context) {
    return IconButton(
      tooltip: 'Обновить динамику',
      onPressed: onRefresh,
      visualDensity: VisualDensity.compact,
      constraints: const BoxConstraints.tightFor(width: 30, height: 30),
      padding: EdgeInsets.zero,
      icon: const Icon(Icons.refresh_rounded, size: 16),
    );
  }
}

class _ReviewDynamicsStatusPill extends StatelessWidget {
  const _ReviewDynamicsStatusPill({
    required this.status,
    required this.tooltip,
  });

  final _ReviewDynamicsStatus status;
  final String tooltip;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: status.color.withValues(alpha: 0.14),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: status.color.withValues(alpha: 0.34)),
        ),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 4),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(status.icon, size: 13, color: status.color),
              const SizedBox(width: 4),
              Text(
                status.label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  color: status.color,
                  fontSize: 11,
                  fontWeight: FontWeight.w800,
                  height: 1,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ReviewDynamicsMetricChip extends StatelessWidget {
  const _ReviewDynamicsMetricChip({
    required this.icon,
    required this.label,
    required this.tooltip,
    this.danger = false,
  });

  final IconData icon;
  final String label;
  final String tooltip;
  final bool danger;

  @override
  Widget build(BuildContext context) {
    final color = danger ? const Color(0xFFB91C1C) : _deepSkyText;

    return Tooltip(
      message: tooltip,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: 0.56),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: color.withValues(alpha: 0.22)),
        ),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 4),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 13, color: color),
              const SizedBox(width: 4),
              Text(
                label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  color: color,
                  fontSize: 11,
                  fontWeight: FontWeight.w800,
                  height: 1,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ReviewDynamicsStatus {
  const _ReviewDynamicsStatus({
    required this.label,
    required this.icon,
    required this.color,
  });

  final String label;
  final IconData icon;
  final Color color;
}

_ReviewDynamicsStatus _reviewDynamicsStatusFor(ReviewDynamics dynamics) {
  switch (dynamics.dynamicsStatus) {
    case 'active_growth':
      return const _ReviewDynamicsStatus(
        label: 'Рост',
        icon: Icons.trending_up_rounded,
        color: Color(0xFF15803D),
      );
    case 'risk_growth':
      return const _ReviewDynamicsStatus(
        label: 'Риск',
        icon: Icons.warning_amber_rounded,
        color: Color(0xFFB91C1C),
      );
    case 'new_activity':
      return const _ReviewDynamicsStatus(
        label: 'Новая активность',
        icon: Icons.fiber_new_rounded,
        color: Color(0xFF0F766E),
      );
    case 'decline':
      return const _ReviewDynamicsStatus(
        label: 'Снижение',
        icon: Icons.trending_down_rounded,
        color: Color(0xFFB45309),
      );
    case 'no_recent_reviews':
      return const _ReviewDynamicsStatus(
        label: 'Нет свежих',
        icon: Icons.event_busy_rounded,
        color: Color(0xFF64748B),
      );
    case 'stable':
      return const _ReviewDynamicsStatus(
        label: 'Стабильно',
        icon: Icons.trending_flat_rounded,
        color: Color(0xFF2563EB),
      );
    default:
      return const _ReviewDynamicsStatus(
        label: 'Динамика',
        icon: Icons.insights_rounded,
        color: _deepSkyText,
      );
  }
}

String _reviewDynamicsGrowthText(int value) {
  if (value > 0) {
    return '+$value к пред. 30д';
  }
  if (value < 0) {
    return '$value к пред. 30д';
  }
  return 'без изменений';
}

String _reviewDynamicsStatusTooltip(ReviewDynamics dynamics) {
  switch (dynamics.dynamicsStatus) {
    case 'active_growth':
      return 'За последние 30 дней отзывов больше, чем за предыдущие 30 дней.';
    case 'risk_growth':
      return 'Количество негативных отзывов за последние 30 дней выросло.';
    case 'stable':
      return 'Количество отзывов за последние 30 дней примерно на уровне предыдущего периода.';
    case 'decline':
      return 'За последние 30 дней отзывов меньше, чем за предыдущие 30 дней.';
    case 'no_recent_reviews':
      return 'За последние 30 дней новых отзывов не найдено.';
    case 'new_activity':
      return 'Раньше отзывов не было, но за последние 30 дней они появились.';
    default:
      return 'Статус рассчитан по сравнению свежих отзывов с предыдущим периодом.';
  }
}

String _reviewDynamicsThirtyDayTooltip(ReviewDynamics dynamics) {
  return 'Отзывы за последние 30 дней: ${dynamics.reviewsLast30Days}.\n'
      'Предыдущие 30 дней: ${dynamics.reviewsPrevious30Days}.\n'
      'Изменение: ${_reviewDynamicsSignedReviewCountText(dynamics.growth30dAbs)}.\n'
      'Текущий период включает дату анализа и предыдущие 29 дней.';
}

String _reviewDynamicsPeriodTooltip({
  required int days,
  required int current,
  required int previous,
  required int growth,
}) {
  return 'Количество новых отзывов за последние $days дней.\n'
      'Текущее значение: $current.\n'
      'Предыдущие $days дней: $previous.\n'
      'Изменение: ${_reviewDynamicsSignedReviewCountText(growth)}.';
}

String _reviewDynamicsRatingTooltip(ReviewDynamics dynamics) {
  return 'Средняя оценка свежих отзывов за последние 30 дней: '
      '${_reviewDynamicsRatingValueText(dynamics.currentRating)}.\n'
      'Средняя оценка за предыдущие 30 дней: '
      '${_reviewDynamicsRatingValueText(dynamics.avgRatingPrevious30Days)}.\n'
      'Изменение рейтинга: '
      '${_reviewDynamicsRatingChangeText(dynamics.ratingChange30d)}.';
}

String _reviewDynamicsRatingText(double? value) {
  if (value == null) {
    return 'рейтинг н/д';
  }
  return 'рейтинг ${value.toStringAsFixed(1)}';
}

String _reviewDynamicsNegativeText(ReviewDynamics dynamics) {
  final share = _reviewDynamicsPercentText(dynamics.negativeShareLast30Days);
  if (share.isEmpty) {
    return 'негатив ${dynamics.negativeReviewsLast30Days}';
  }
  return 'негатив ${dynamics.negativeReviewsLast30Days} · $share';
}

String _reviewDynamicsNegativeTooltip(ReviewDynamics dynamics) {
  return 'Негативные отзывы — оценки 1 или 2.\n'
      'За последние 30 дней: ${dynamics.negativeReviewsLast30Days}.\n'
      'Доля негатива за последние 30 дней: '
      '${_reviewDynamicsPercentValueText(dynamics.negativeShareLast30Days)}.\n'
      'Всего негативных отзывов за период анализа: '
      '${dynamics.negativeReviewsCount}.';
}

String _reviewDynamicsPercentText(double? value) {
  if (value == null) {
    return '';
  }
  final normalized = value.abs() <= 1 ? value * 100 : value;
  return '${normalized.round()}%';
}

String _reviewDynamicsPercentValueText(double? value) {
  final text = _reviewDynamicsPercentText(value);
  return text.isEmpty ? 'нет данных' : text;
}

String _reviewDynamicsRatingValueText(double? value) {
  if (value == null) {
    return 'нет данных';
  }
  return value.toStringAsFixed(1);
}

String _reviewDynamicsRatingChangeText(double? value) {
  if (value == null) {
    return 'нет данных';
  }
  return _reviewDynamicsSignedDecimalText(value);
}

String _reviewDynamicsSignedReviewCountText(int value) {
  return '${_reviewDynamicsSignedIntText(value)} ${_reviewDynamicsReviewWord(value)}';
}

String _reviewDynamicsSignedIntText(int value) {
  if (value > 0) {
    return '+$value';
  }
  return '$value';
}

String _reviewDynamicsSignedDecimalText(double value) {
  final text = value.toStringAsFixed(1);
  if (value > 0) {
    return '+$text';
  }
  return text;
}

String _reviewDynamicsReviewWord(int value) {
  final absolute = value.abs();
  final lastTwoDigits = absolute % 100;
  if (lastTwoDigits >= 11 && lastTwoDigits <= 14) {
    return 'отзывов';
  }

  final lastDigit = absolute % 10;
  if (lastDigit == 1) {
    return 'отзыв';
  }
  if (lastDigit >= 2 && lastDigit <= 4) {
    return 'отзыва';
  }
  return 'отзывов';
}

class _OrganizationDetails extends StatelessWidget {
  const _OrganizationDetails({
    required this.organization,
    required this.reviewsAnalysisLoading,
    required this.radiusReviewsAnalysisLoading,
    required this.radiusM,
    required this.onAnalyzeReviews,
    required this.onAnalyzeRadiusReviews,
  });

  final Organization organization;
  final bool reviewsAnalysisLoading;
  final bool radiusReviewsAnalysisLoading;
  final int radiusM;
  final VoidCallback onAnalyzeReviews;
  final VoidCallback onAnalyzeRadiusReviews;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;

    return DecoratedBox(
      decoration: BoxDecoration(
        color: _fadedSky,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: _fadedSkyBorder),
      ),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              organization.displayTitle,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: textTheme.titleSmall?.copyWith(
                color: _deepSkyText,
                fontWeight: FontWeight.w700,
              ),
            ),
            if (organization.categoryLabel.isNotEmpty)
              _InfoLine(
                icon: Icons.category_rounded,
                text: organization.categoryLabel,
              ),
            if (organization.fullAddress.isNotEmpty)
              _InfoLine(
                icon: Icons.place_rounded,
                text: organization.fullAddress,
              ),
            if (organization.phone.isNotEmpty)
              _InfoLine(icon: Icons.phone_rounded, text: organization.phone),
            if (organization.orgUrl.isNotEmpty ||
                organization.websiteUrl.isNotEmpty)
              _ExternalLinksLine(
                onYandexTap: organization.orgUrl.isEmpty
                    ? null
                    : () => _launchExternalUrl(organization.orgUrl),
                onWebsiteTap: organization.websiteUrl.isEmpty
                    ? null
                    : () => _launchExternalUrl(organization.websiteUrl),
              ),
            if (organization.openStatusText.isNotEmpty)
              _InfoLine(
                icon: Icons.schedule_rounded,
                text: organization.openStatusText,
              ),
            if (organization.ratingValue.isNotEmpty)
              _InfoLine(
                icon: Icons.star_rounded,
                text: _ratingText(organization),
              ),
            if (organization.ratingCount != null &&
                organization.ratingCount! > 0)
              _ReviewsInfoLine(
                text: '${organization.ratingCount} отзывов',
                loading: reviewsAnalysisLoading,
                radiusLabel: _radiusLabelFor(radiusM),
                radiusLoading: radiusReviewsAnalysisLoading,
                onAnalyze: onAnalyzeReviews,
                onAnalyzeRadius: onAnalyzeRadiusReviews,
              ),
            if (_promotionText(organization).isNotEmpty)
              _InfoLine(
                icon: Icons.local_offer_rounded,
                text: _promotionText(organization),
                maxLines: 3,
              ),
            if (_flagsText(organization).isNotEmpty)
              _InfoLine(
                icon: Icons.check_circle_rounded,
                text: _flagsText(organization),
                maxLines: 3,
              ),
            if (organization.services.isNotEmpty)
              _InfoLine(
                icon: Icons.medical_services_rounded,
                text: 'Услуги: ${_itemsText(organization.services)}',
                maxLines: 3,
              ),
            if (organization.paymentMethods.isNotEmpty)
              _InfoLine(
                icon: Icons.credit_card_rounded,
                text: 'Оплата: ${_itemsText(organization.paymentMethods)}',
                maxLines: 3,
              ),
            if (organization.medicalSpecialists.isNotEmpty)
              _InfoLine(
                icon: Icons.badge_rounded,
                text:
                    'Врачи и специалисты: '
                    '${_itemsText(organization.medicalSpecialists)}',
                maxLines: 3,
              ),
            if (organization.unifiedMedicalSpecialists.isNotEmpty)
              _InfoLine(
                icon: Icons.health_and_safety_rounded,
                text:
                    'Медицинские специализации: '
                    '${_itemsText(organization.unifiedMedicalSpecialists)}',
                maxLines: 3,
              ),
            if (organization.pediatricSpecialists.isNotEmpty)
              _InfoLine(
                icon: Icons.child_care_rounded,
                text:
                    'Детские специалисты: '
                    '${_itemsText(organization.pediatricSpecialists)}',
                maxLines: 2,
              ),
            if (organization.accessibility.isNotEmpty)
              _InfoLine(
                icon: Icons.accessible_rounded,
                text: 'Доступность: ${_itemsText(organization.accessibility)}',
                maxLines: 2,
              ),
          ],
        ),
      ),
    );
  }

  String _ratingText(Organization organization) {
    return 'Рейтинг ${organization.ratingValue}';
  }

  String _promotionText(Organization organization) {
    final parts = <String>[
      if (organization.awardsText.isNotEmpty) organization.awardsText,
      if (organization.promotionTypes.isNotEmpty)
        'Акции: ${_itemsText(organization.promotionTypes)}',
      if (organization.snippetPriceText.isNotEmpty)
        'Цена: ${organization.snippetPriceText}',
      if (organization.snippetOfferText.isNotEmpty)
        organization.snippetOfferText,
      if (organization.cashbackPercent.isNotEmpty)
        'Кешбэк: ${organization.cashbackPercent}',
    ];
    return parts.join(' · ');
  }

  String _flagsText(Organization organization) {
    final flags = <String>[
      if (organization.hasGoodPlace) 'Хорошее место',
      if (organization.hasFreeExamination) 'Бесплатная консультация',
      if (organization.hasGuarantee) 'Гарантия',
      if (organization.hasForChildren) 'Есть детский кабинет',
      if (organization.hasWifi) 'Wi-Fi',
      if (organization.hasCardPayment) 'Оплата картой',
      if (organization.hasInstallments) 'Рассрочка',
      if (organization.hasRamp) 'Пандус',
      if (organization.hasDisabledParking) 'Парковка для инвалидов',
      if (organization.businessVerifiedOwner) 'Подтвержден владелец',
    ];
    return flags.join(' · ');
  }

  String _itemsText(List<FeatureItem> items) {
    final names = items
        .map((item) => item.displayName)
        .where((name) => name.isNotEmpty)
        .toSet()
        .toList(growable: false);
    return names.join(', ');
  }

  Future<void> _launchExternalUrl(String rawUrl) async {
    final uri = _externalUri(rawUrl);
    if (uri == null) {
      return;
    }
    await launchUrl(uri, mode: LaunchMode.externalApplication);
  }

  Uri? _externalUri(String rawUrl) {
    final trimmed = rawUrl.trim();
    if (trimmed.isEmpty) {
      return null;
    }

    final withScheme = trimmed.contains('://') ? trimmed : 'https://$trimmed';
    final uri = Uri.tryParse(withScheme);
    if (uri == null || !uri.hasScheme || uri.host.isEmpty) {
      return null;
    }
    return uri;
  }
}

class _ReviewsInfoLine extends StatelessWidget {
  const _ReviewsInfoLine({
    required this.text,
    required this.loading,
    required this.radiusLabel,
    required this.radiusLoading,
    required this.onAnalyze,
    required this.onAnalyzeRadius,
  });

  final String text;
  final bool loading;
  final String radiusLabel;
  final bool radiusLoading;
  final VoidCallback onAnalyze;
  final VoidCallback onAnalyzeRadius;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;

    return Padding(
      padding: const EdgeInsets.only(top: 7),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          const SizedBox(
            width: 20,
            child: Icon(
              Icons.chat_bubble_rounded,
              size: 16,
              color: _deepSkyText,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              text,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: textTheme.bodySmall?.copyWith(color: _deepSkyText),
            ),
          ),
          const SizedBox(width: 8),
          IconButton.filledTonal(
            tooltip: 'Проанализировать отзывы',
            onPressed: loading ? null : onAnalyze,
            visualDensity: VisualDensity.compact,
            constraints: const BoxConstraints.tightFor(width: 34, height: 34),
            padding: EdgeInsets.zero,
            icon: loading
                ? const SizedBox.square(
                    dimension: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.auto_awesome_rounded, size: 18),
          ),
          const SizedBox(width: 6),
          IconButton.filledTonal(
            tooltip: 'Проанализировать организации в радиусе $radiusLabel',
            onPressed: radiusLoading ? null : onAnalyzeRadius,
            visualDensity: VisualDensity.compact,
            constraints: const BoxConstraints.tightFor(width: 34, height: 34),
            padding: EdgeInsets.zero,
            icon: radiusLoading
                ? const SizedBox.square(
                    dimension: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.radar_rounded, size: 18),
          ),
        ],
      ),
    );
  }
}

class _InfoLine extends StatelessWidget {
  const _InfoLine({required this.icon, required this.text, this.maxLines = 2});

  final IconData icon;
  final String text;
  final int maxLines;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;

    return Padding(
      padding: const EdgeInsets.only(top: 7),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(width: 20, child: Icon(icon, size: 16, color: _deepSkyText)),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              text,
              maxLines: maxLines,
              overflow: TextOverflow.ellipsis,
              style: textTheme.bodySmall?.copyWith(color: _deepSkyText),
            ),
          ),
        ],
      ),
    );
  }
}

class _ExternalLinksLine extends StatelessWidget {
  const _ExternalLinksLine({this.onYandexTap, this.onWebsiteTap});

  final VoidCallback? onYandexTap;
  final VoidCallback? onWebsiteTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 7),
      child: Row(
        children: [
          if (onYandexTap != null)
            _ExternalLinkButton(
              icon: Icons.map_outlined,
              tooltip: 'Открыть в Яндекс.Картах',
              onTap: onYandexTap!,
            ),
          if (onWebsiteTap != null)
            _ExternalLinkButton(
              icon: Icons.language,
              tooltip: 'Открыть сайт',
              onTap: onWebsiteTap!,
            ),
        ],
      ),
    );
  }
}

class _ExternalLinkButton extends StatelessWidget {
  const _ExternalLinkButton({
    required this.icon,
    required this.tooltip,
    required this.onTap,
  });

  final IconData icon;
  final String tooltip;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return IconButton(
      onPressed: onTap,
      icon: Icon(icon),
      tooltip: tooltip,
      color: _selectedSkyBorder,
      visualDensity: VisualDensity.compact,
      constraints: const BoxConstraints.tightFor(width: 36, height: 36),
      padding: EdgeInsets.zero,
    );
  }
}

class _OrganizationsList extends StatelessWidget {
  const _OrganizationsList({
    required this.organizations,
    required this.selectedOrganization,
    required this.hoveredOrganizationId,
    required this.nearestRanksById,
    required this.onSelected,
    required this.onHoverChanged,
  });

  final List<Organization> organizations;
  final Organization? selectedOrganization;
  final String? hoveredOrganizationId;
  final Map<String, int> nearestRanksById;
  final ValueChanged<Organization> onSelected;
  final ValueChanged<String?> onHoverChanged;

  @override
  Widget build(BuildContext context) {
    final selectedPosition = selectedOrganization == null
        ? null
        : _validPositionFor(selectedOrganization!);

    return ListView.separated(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      itemCount: organizations.length,
      separatorBuilder: (context, index) => const SizedBox(height: 8),
      itemBuilder: (context, index) {
        final organization = organizations[index];
        final selected = selectedOrganization?.id == organization.id;
        final hovered = hoveredOrganizationId == organization.id;
        final rank = nearestRanksById[organization.id];
        final background = selected
            ? _selectedSky
            : hovered
            ? Colors.white.withAlpha(245)
            : _fadedSky;
        final borderColor = selected || hovered
            ? _selectedSkyBorder
            : _fadedSkyBorder;

        return MouseRegion(
          onEnter: (_) => onHoverChanged(organization.id),
          onExit: (_) => onHoverChanged(null),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 180),
            curve: Curves.easeOut,
            decoration: BoxDecoration(
              color: background,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(
                color: borderColor,
                width: selected || hovered ? 2 : 1,
              ),
              boxShadow: selected || hovered
                  ? [
                      BoxShadow(
                        color: _selectedSkyBorder.withValues(
                          alpha: selected ? 0.22 : 0.14,
                        ),
                        blurRadius: selected ? 16 : 10,
                        offset: const Offset(0, 6),
                      ),
                    ]
                  : null,
            ),
            child: ListTile(
              selected: selected,
              contentPadding: const EdgeInsets.symmetric(
                horizontal: 12,
                vertical: 6,
              ),
              leading: _IconBadge(
                icon: _organizationIcon(organization),
                selected: selected || hovered,
              ),
              title: Row(
                children: [
                  if (rank != null) ...[
                    _RankBadge(rank: rank),
                    const SizedBox(width: 7),
                  ],
                  Expanded(
                    child: Text(
                      organization.displayTitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        color: _deepSkyText,
                        fontWeight: selected || hovered
                            ? FontWeight.w800
                            : FontWeight.w700,
                      ),
                    ),
                  ),
                ],
              ),
              subtitle: Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  _subtitleFor(organization, selectedPosition),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: _deepSkyText.withValues(alpha: 0.78)),
                ),
              ),
              trailing: organization.ratingValue.isEmpty
                  ? null
                  : _RatingBadge(value: organization.ratingValue),
              onTap: () => onSelected(organization),
            ),
          ),
        );
      },
    );
  }

  String _subtitleFor(Organization organization, LatLng? selectedPosition) {
    final distanceText = _distanceTextFor(organization, selectedPosition);
    final parts = <String>[
      if (distanceText.isNotEmpty) distanceText,
      if (organization.category.isNotEmpty) organization.category,
      if (organization.ratingValue.isNotEmpty)
        'Рейтинг ${organization.ratingValue}',
      if (organization.ratingCount != null && organization.ratingCount! > 0)
        '${organization.ratingCount} отзывов',
      if (organization.fullAddress.isNotEmpty) organization.fullAddress,
    ];

    if (parts.isEmpty) {
      return '${organization.lat.toStringAsFixed(5)}, '
          '${organization.lon.toStringAsFixed(5)}';
    }
    return parts.join(' · ');
  }

  String _distanceTextFor(Organization organization, LatLng? selectedPosition) {
    if (selectedPosition == null) {
      return '';
    }

    final position = _validPositionFor(organization);
    if (position == null) {
      return '';
    }

    return _distanceLabelFor(_radiusDistance(selectedPosition, position));
  }
}

class _RankBadge extends StatelessWidget {
  const _RankBadge({required this.rank});

  final int rank;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.72),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: _fadedSkyBorder),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
        child: Text(
          '#$rank',
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(
            color: _deepSkyText,
            fontSize: 11,
            fontWeight: FontWeight.w900,
            height: 1,
          ),
        ),
      ),
    );
  }
}

class _RatingBadge extends StatelessWidget {
  const _RatingBadge({required this.value});

  final String value;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.72),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: _fadedSkyBorder),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.star_rounded, size: 16, color: _deepSkyText),
          const SizedBox(width: 3),
          Text(
            value,
            style: const TextStyle(
              color: _deepSkyText,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }
}

class _LoadingState extends StatelessWidget {
  const _LoadingState();

  @override
  Widget build(BuildContext context) {
    return const Center(
      child: SizedBox(
        width: 48,
        height: 48,
        child: CircularProgressIndicator(),
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.apiBaseUrl, required this.onRetry});

  final String apiBaseUrl;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return _MessageState(
      icon: Icons.map_outlined,
      title: 'Нет данных для карты',
      message:
          'Источник: $apiBaseUrl${OrganizationsApiClient.organizationsPath}',
      actionLabel: 'Обновить',
      onAction: onRetry,
    );
  }
}

class _NoFilterResultsState extends StatelessWidget {
  const _NoFilterResultsState({required this.summary, required this.onReset});

  final String summary;
  final VoidCallback onReset;

  @override
  Widget build(BuildContext context) {
    return _MessageState(
      icon: Icons.filter_alt_off_rounded,
      title: 'Нет точек по фильтрам',
      message: summary.isEmpty ? 'Сбросьте фильтры' : summary,
      actionLabel: 'Показать все',
      onAction: onReset,
    );
  }
}

class _ErrorState extends StatelessWidget {
  const _ErrorState({
    required this.message,
    required this.apiBaseUrl,
    required this.onRetry,
  });

  final String message;
  final String apiBaseUrl;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return _MessageState(
      icon: Icons.cloud_off_rounded,
      title: 'API недоступен',
      message:
          '$message\n$apiBaseUrl${OrganizationsApiClient.organizationsPath}',
      actionLabel: 'Повторить',
      onAction: onRetry,
    );
  }
}

class _MessageState extends StatelessWidget {
  const _MessageState({
    required this.icon,
    required this.title,
    required this.message,
    required this.actionLabel,
    required this.onAction,
  });

  final IconData icon;
  final String title;
  final String message;
  final String actionLabel;
  final VoidCallback onAction;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final textTheme = Theme.of(context).textTheme;

    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 420),
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 44, color: colorScheme.primary),
              const SizedBox(height: 14),
              Text(
                title,
                textAlign: TextAlign.center,
                style: textTheme.titleLarge?.copyWith(
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 8),
              Text(
                message,
                textAlign: TextAlign.center,
                style: textTheme.bodyMedium?.copyWith(
                  color: colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 18),
              FilledButton.icon(
                onPressed: onAction,
                icon: const Icon(Icons.refresh_rounded),
                label: Text(actionLabel),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
