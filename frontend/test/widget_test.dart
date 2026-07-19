import 'package:flutter_test/flutter_test.dart';

import 'package:ui/main.dart';

void main() {
  testWidgets('shows empty state when API returns no organizations', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(MyApp(organizationsLoader: (_) async => []));

    expect(find.byType(MyApp), findsOneWidget);

    await tester.pump();

    expect(find.text('Нет данных для карты'), findsOneWidget);
  });
}
