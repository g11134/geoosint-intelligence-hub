import 'package:latlong2/latlong.dart';

class FeatureItem {
  const FeatureItem({required this.id, required this.name});

  final String id;
  final String name;

  String get displayName => name.isNotEmpty ? name : id;

  factory FeatureItem.fromJson(Object? value) {
    final map = Organization._readMap(value);
    final id = Organization._readString(map['id']);
    final name = Organization._readString(map['name']);

    return FeatureItem(id: id, name: name);
  }
}

class Organization {
  const Organization({
    required this.id,
    required this.title,
    required this.shortTitle,
    required this.fullAddress,
    required this.category,
    required this.categoryItems,
    required this.phone,
    required this.lat,
    required this.lon,
    required this.permalink,
    required this.orgUrl,
    required this.websiteUrl,
    required this.ratingCount,
    required this.ratingValue,
    required this.reviewCount,
    required this.sourceQuery,
    required this.openStatusText,
    required this.awardsText,
    required this.businessVerifiedOwner,
    required this.services,
    required this.paymentMethods,
    required this.medicalSpecialists,
    required this.unifiedMedicalSpecialists,
    required this.pediatricSpecialists,
    required this.accessibility,
    required this.promotionTypes,
    required this.cashbackPercent,
    required this.snippetPriceText,
    required this.snippetOfferText,
    required this.hasForChildren,
    required this.hasGoodPlace,
    required this.hasVtbOffer,
    required this.hasFreeExamination,
    required this.hasInstallments,
    required this.hasGuarantee,
    required this.hasWifi,
    required this.hasRamp,
    required this.hasDisabledParking,
  });

  final String id;
  final String title;
  final String shortTitle;
  final String fullAddress;
  final String category;
  final List<FeatureItem> categoryItems;
  final String phone;
  final double lat;
  final double lon;
  final String permalink;
  final String orgUrl;
  final String websiteUrl;
  final int? ratingCount;
  final String ratingValue;
  final int? reviewCount;
  final String sourceQuery;
  final String openStatusText;
  final String awardsText;
  final bool businessVerifiedOwner;
  final List<FeatureItem> services;
  final List<FeatureItem> paymentMethods;
  final List<FeatureItem> medicalSpecialists;
  final List<FeatureItem> unifiedMedicalSpecialists;
  final List<FeatureItem> pediatricSpecialists;
  final List<FeatureItem> accessibility;
  final List<FeatureItem> promotionTypes;
  final String cashbackPercent;
  final String snippetPriceText;
  final String snippetOfferText;
  final bool hasForChildren;
  final bool hasGoodPlace;
  final bool hasVtbOffer;
  final bool hasFreeExamination;
  final bool hasInstallments;
  final bool hasGuarantee;
  final bool hasWifi;
  final bool hasRamp;
  final bool hasDisabledParking;

  LatLng get position => LatLng(lat, lon);

  String get displayTitle {
    if (title.isNotEmpty) {
      return title;
    }
    if (shortTitle.isNotEmpty) {
      return shortTitle;
    }
    return 'Без названия';
  }

  List<String> get categoryNames {
    final names = categoryItems
        .map((item) => item.displayName)
        .where((name) => name.isNotEmpty)
        .toSet()
        .toList(growable: false);

    if (names.isNotEmpty) {
      return names;
    }
    return category.isEmpty ? const [] : [category];
  }

  String get categoryLabel => categoryNames.join(', ');

  bool get hasCardPayment {
    if (paymentMethods.any(
      (item) => _matchesAny(item, const [
        'payment_card',
        'by_credit_card',
        'credit',
        'карта',
        'картой',
      ]),
    )) {
      return true;
    }
    return false;
  }

  List<FeatureItem> get allSpecialists => [
    ...medicalSpecialists,
    ...unifiedMedicalSpecialists,
    ...pediatricSpecialists,
  ];

  factory Organization.fromJson(Map<String, dynamic> json) {
    final coordinates = _readMap(json['coordinates']);
    final lat = _readDouble(json['lat'] ?? coordinates['lat']);
    final lon = _readDouble(json['lon'] ?? coordinates['lon']);

    if (lat == null || lon == null) {
      throw FormatException('Organization has invalid coordinates: $json');
    }

    final rating = _readMap(json['rating']);
    final source = _readMap(json['source']);
    final status = _readMap(json['status']);
    final features = _readMap(json['features']);
    final specialists = _readMap(features['specialists']);
    final promotions = _readMap(features['promotions']);
    final flags = _readMap(features['flags']);
    final title = _readString(json['title']);
    final shortTitle = _readString(json['shortTitle']);
    final permalink = _readString(json['permalink']);
    final id = _readString(json['id']);
    final category = _readString(json['category'] ?? json['categories']);

    return Organization(
      id: id.isNotEmpty ? id : _fallbackId(permalink, title, lat, lon),
      title: title,
      shortTitle: shortTitle,
      fullAddress: _readString(json['fullAddress']),
      category: category,
      categoryItems: _readItems(json['categories']),
      phone: _readString(json['phones_0_number'] ?? json['phone']),
      lat: lat,
      lon: lon,
      permalink: permalink,
      orgUrl: _readString(json['orgUrl'] ?? json['org_url']),
      websiteUrl: _readString(json['websiteUrl'] ?? json['website_url']),
      ratingCount: _readInt(
        json['ratingData_ratingCount'] ?? rating['count'] ?? rating['countRaw'],
      ),
      ratingValue: _readRatingValue(
        json['ratingData_ratingValue'] ?? rating['value'] ?? rating['valueRaw'],
      ),
      reviewCount: _readInt(rating['reviewCount']),
      sourceQuery: _readString(source['query']),
      openStatusText: _readString(status['openStatusText']),
      awardsText: _readString(status['awardsText']),
      businessVerifiedOwner: _readBool(status['businessVerifiedOwner']),
      services: _readItems(features['services']),
      paymentMethods: _readItems(features['paymentMethods']),
      medicalSpecialists: _readItems(specialists['medical']),
      unifiedMedicalSpecialists: _readItems(specialists['unifiedMedical']),
      pediatricSpecialists: _readItems(specialists['pediatric']),
      accessibility: _readItems(features['accessibility']),
      promotionTypes: _readItems(promotions['types']),
      cashbackPercent: _readString(promotions['cashbackPercent']),
      snippetPriceText: _readString(promotions['snippetPriceText']),
      snippetOfferText: _readString(promotions['snippetOfferText']),
      hasForChildren: _readBool(flags['forChildren']),
      hasGoodPlace: _readBool(promotions['hasGoodPlace']),
      hasVtbOffer: _readBool(promotions['hasVtbOffer']),
      hasFreeExamination: _readBool(promotions['hasFreeExamination']),
      hasInstallments: _readBool(promotions['hasInstallments']),
      hasGuarantee: _readBool(flags['guarantee']),
      hasWifi: _readBool(flags['wiFi']),
      hasRamp: _readBool(flags['ramp']),
      hasDisabledParking: _readBool(flags['disabledParking']),
    );
  }

  static Map<String, dynamic> _readMap(Object? value) {
    if (value is Map<String, dynamic>) {
      return value;
    }
    if (value is Map) {
      return Map<String, dynamic>.from(value);
    }
    return const {};
  }

  static List<FeatureItem> _readItems(Object? value) {
    if (value is! List) {
      return const [];
    }

    final items = <FeatureItem>[];
    final seen = <String>{};
    for (final rawItem in value) {
      final item = FeatureItem.fromJson(rawItem);
      final key = '${item.id}|${item.name}';
      if (item.displayName.isEmpty || seen.contains(key)) {
        continue;
      }
      seen.add(key);
      items.add(item);
    }
    return List.unmodifiable(items);
  }

  static String _readString(Object? value) {
    if (value == null) {
      return '';
    }
    if (value is List || value is Map) {
      return '';
    }
    return value.toString().trim();
  }

  static String _readRatingValue(Object? value) {
    if (value is num) {
      return value.toStringAsFixed(1);
    }
    return _readString(value);
  }

  static bool _readBool(Object? value) {
    if (value is bool) {
      return value;
    }
    if (value is num) {
      return value != 0;
    }
    if (value is String) {
      final normalized = value.trim().toLowerCase();
      return normalized == '1' ||
          normalized == 'true' ||
          normalized == 'yes' ||
          normalized == 'да';
    }
    return false;
  }

  static double? _readDouble(Object? value) {
    if (value is num) {
      return value.toDouble();
    }
    if (value is String) {
      return double.tryParse(value.trim().replaceAll(',', '.'));
    }
    return null;
  }

  static int? _readInt(Object? value) {
    if (value is int) {
      return value;
    }
    if (value is num) {
      return value.toInt();
    }
    if (value is String) {
      return int.tryParse(value.trim());
    }
    return null;
  }

  static bool _matchesAny(FeatureItem item, List<String> needles) {
    final value = '${item.id} ${item.name}'.toLowerCase();
    return needles.any(value.contains);
  }

  static String _fallbackId(
    String permalink,
    String title,
    double lat,
    double lon,
  ) {
    if (permalink.isNotEmpty) {
      return permalink;
    }
    return '${title}_${lat.toStringAsFixed(6)}_${lon.toStringAsFixed(6)}';
  }
}
