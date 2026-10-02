// Smoke test: the app boots to the splash screen without throwing.
// The old counter-app test was for the default Flutter template's MyApp,
// which no longer exists -- this checks our real entry point instead.
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/main.dart';

void main() {
  testWidgets('App boots to splash screen', (WidgetTester tester) async {
    await tester.pumpWidget(const GoogleHoraApp());
    await tester.pump();
    expect(find.byType(GoogleHoraApp), findsOneWidget);
  });
}
