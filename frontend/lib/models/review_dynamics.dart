class ReviewDynamics {
  const ReviewDynamics({
    required this.organizationKey,
    required this.organizationTitle,
    required this.address,
    required this.totalReviewsSinceStart,
    required this.firstReviewDate,
    required this.lastReviewDate,
    required this.daysWithReviews,
    required this.reviewsLast7Days,
    required this.reviewsLast30Days,
    required this.reviewsLast90Days,
    required this.reviewsPrevious7Days,
    required this.reviewsPrevious30Days,
    required this.reviewsPrevious90Days,
    required this.growth7dAbs,
    required this.growth30dAbs,
    required this.growth90dAbs,
    required this.growth7dPct,
    required this.growth30dPct,
    required this.growth90dPct,
    required this.avgReviewRating,
    required this.avgRatingLast30Days,
    required this.avgRatingPrevious30Days,
    required this.ratingChange30d,
    required this.positiveReviewsCount,
    required this.neutralReviewsCount,
    required this.negativeReviewsCount,
    required this.negativeShare,
    required this.negativeReviewsLast30Days,
    required this.negativeShareLast30Days,
    required this.dynamicsStatus,
  });

  final String organizationKey;
  final String organizationTitle;
  final String address;
  final int totalReviewsSinceStart;
  final String firstReviewDate;
  final String lastReviewDate;
  final int daysWithReviews;
  final int reviewsLast7Days;
  final int reviewsLast30Days;
  final int reviewsLast90Days;
  final int reviewsPrevious7Days;
  final int reviewsPrevious30Days;
  final int reviewsPrevious90Days;
  final int growth7dAbs;
  final int growth30dAbs;
  final int growth90dAbs;
  final double? growth7dPct;
  final double? growth30dPct;
  final double? growth90dPct;
  final double? avgReviewRating;
  final double? avgRatingLast30Days;
  final double? avgRatingPrevious30Days;
  final double? ratingChange30d;
  final int positiveReviewsCount;
  final int neutralReviewsCount;
  final int negativeReviewsCount;
  final double? negativeShare;
  final int negativeReviewsLast30Days;
  final double? negativeShareLast30Days;
  final String dynamicsStatus;

  bool get hasRecentReviews => reviewsLast30Days > 0;

  bool get hasNegativeRisk =>
      dynamicsStatus == 'risk_growth' || negativeReviewsLast30Days > 0;

  double? get currentRating => avgRatingLast30Days ?? avgReviewRating;

  factory ReviewDynamics.fromJson(Map<String, dynamic> json) {
    return ReviewDynamics(
      organizationKey: _readString(
        _pick(json, const ['organization_key', 'organizationKey', 'id']),
      ),
      organizationTitle: _readString(
        _pick(json, const ['organization_title', 'organizationTitle', 'title']),
      ),
      address: _readString(
        _pick(json, const [
          'address',
          'organization_address',
          'organizationAddress',
        ]),
      ),
      totalReviewsSinceStart: _readInt(
        _pick(json, const [
          'total_reviews_since_start',
          'totalReviewsSinceStart',
        ]),
      ),
      firstReviewDate: _readString(
        _pick(json, const ['first_review_date', 'firstReviewDate']),
      ),
      lastReviewDate: _readString(
        _pick(json, const ['last_review_date', 'lastReviewDate']),
      ),
      daysWithReviews: _readInt(
        _pick(json, const ['days_with_reviews', 'daysWithReviews']),
      ),
      reviewsLast7Days: _readInt(
        _pick(json, const ['reviews_last_7_days', 'reviewsLast7Days']),
      ),
      reviewsLast30Days: _readInt(
        _pick(json, const ['reviews_last_30_days', 'reviewsLast30Days']),
      ),
      reviewsLast90Days: _readInt(
        _pick(json, const ['reviews_last_90_days', 'reviewsLast90Days']),
      ),
      reviewsPrevious7Days: _readInt(
        _pick(json, const ['reviews_previous_7_days', 'reviewsPrevious7Days']),
      ),
      reviewsPrevious30Days: _readInt(
        _pick(json, const [
          'reviews_previous_30_days',
          'reviewsPrevious30Days',
        ]),
      ),
      reviewsPrevious90Days: _readInt(
        _pick(json, const [
          'reviews_previous_90_days',
          'reviewsPrevious90Days',
        ]),
      ),
      growth7dAbs: _readInt(
        _pick(json, const ['growth_7d_abs', 'growth7dAbs']),
      ),
      growth30dAbs: _readInt(
        _pick(json, const ['growth_30d_abs', 'growth30dAbs']),
      ),
      growth90dAbs: _readInt(
        _pick(json, const ['growth_90d_abs', 'growth90dAbs']),
      ),
      growth7dPct: _readDouble(
        _pick(json, const ['growth_7d_pct', 'growth7dPct']),
      ),
      growth30dPct: _readDouble(
        _pick(json, const ['growth_30d_pct', 'growth30dPct']),
      ),
      growth90dPct: _readDouble(
        _pick(json, const ['growth_90d_pct', 'growth90dPct']),
      ),
      avgReviewRating: _readDouble(
        _pick(json, const ['avg_review_rating', 'avgReviewRating']),
      ),
      avgRatingLast30Days: _readDouble(
        _pick(json, const ['avg_rating_last_30_days', 'avgRatingLast30Days']),
      ),
      avgRatingPrevious30Days: _readDouble(
        _pick(json, const [
          'avg_rating_previous_30_days',
          'avgRatingPrevious30Days',
        ]),
      ),
      ratingChange30d: _readDouble(
        _pick(json, const ['rating_change_30d', 'ratingChange30d']),
      ),
      positiveReviewsCount: _readInt(
        _pick(json, const ['positive_reviews_count', 'positiveReviewsCount']),
      ),
      neutralReviewsCount: _readInt(
        _pick(json, const ['neutral_reviews_count', 'neutralReviewsCount']),
      ),
      negativeReviewsCount: _readInt(
        _pick(json, const ['negative_reviews_count', 'negativeReviewsCount']),
      ),
      negativeShare: _readDouble(
        _pick(json, const ['negative_share', 'negativeShare']),
      ),
      negativeReviewsLast30Days: _readInt(
        _pick(json, const [
          'negative_reviews_last_30_days',
          'negativeReviewsLast30Days',
        ]),
      ),
      negativeShareLast30Days: _readDouble(
        _pick(json, const [
          'negative_share_last_30_days',
          'negativeShareLast30Days',
        ]),
      ),
      dynamicsStatus: _readString(
        _pick(json, const ['dynamics_status', 'dynamicsStatus', 'status']),
      ),
    );
  }

  static ReviewDynamics? fromResponse(Object? decoded) {
    final map = _readMap(decoded);
    if (map.isNotEmpty) {
      return ReviewDynamics.fromJson(map);
    }

    if (decoded is List && decoded.isNotEmpty) {
      final firstMap = _readMap(decoded.first);
      if (firstMap.isNotEmpty) {
        return ReviewDynamics.fromJson(firstMap);
      }
    }

    return null;
  }

  static Object? _pick(Map<String, dynamic> json, List<String> keys) {
    for (final key in keys) {
      if (json.containsKey(key)) {
        return json[key];
      }
    }
    return null;
  }

  static Map<String, dynamic> _readMap(Object? value) {
    if (value is Map<String, dynamic>) {
      return _unwrapMap(value);
    }
    if (value is Map) {
      return _unwrapMap(Map<String, dynamic>.from(value));
    }
    return const {};
  }

  static Map<String, dynamic> _unwrapMap(Map<String, dynamic> value) {
    for (final key in const ['data', 'result']) {
      final nested = value[key];
      if (nested is Map<String, dynamic>) {
        return nested;
      }
      if (nested is Map) {
        return Map<String, dynamic>.from(nested);
      }
      if (nested is List && nested.isNotEmpty) {
        final nestedMap = _readMap(nested.first);
        if (nestedMap.isNotEmpty) {
          return nestedMap;
        }
      }
    }

    final items = value['items'];
    if (items is List && items.isNotEmpty) {
      final nestedMap = _readMap(items.first);
      if (nestedMap.isNotEmpty) {
        return nestedMap;
      }
    }

    return value;
  }

  static String _readString(Object? value) {
    if (value == null || value is List || value is Map) {
      return '';
    }
    return value.toString().trim();
  }

  static int _readInt(Object? value) {
    if (value is int) {
      return value;
    }
    if (value is num) {
      return value.toInt();
    }
    if (value is String) {
      final normalized = value.trim().replaceAll(RegExp(r'\s+'), '');
      if (normalized.isEmpty) {
        return 0;
      }
      return int.tryParse(normalized) ??
          double.tryParse(normalized.replaceAll(',', '.'))?.round() ??
          0;
    }
    return 0;
  }

  static double? _readDouble(Object? value) {
    if (value is num) {
      return value.toDouble();
    }
    if (value is String) {
      final normalized = value.trim().replaceAll(RegExp(r'\s+'), '');
      if (normalized.isEmpty) {
        return null;
      }
      return double.tryParse(normalized.replaceAll(',', '.'));
    }
    return null;
  }
}
