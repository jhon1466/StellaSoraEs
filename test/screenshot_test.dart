// Renders the installer screen to build/android_preview.png for design review.
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:stella_sora_es_patch/main.dart';

void main() {
  testWidgets('android preview', (tester) async {
    for (final (file, weight) in [('fonts/MiSans-Semibold.ttf', 600), ('fonts/MiSans-Bold.ttf', 700)]) {
      final loader = FontLoader('MiSans')..addFont(Future.value(File(file).readAsBytesSync().buffer.asByteData()));
      await loader.load();
      expect(weight, isPositive);
    }
    tester.view.physicalSize = const Size(1080, 2400);
    tester.view.devicePixelRatio = 2.625;
    final key = GlobalKey();
    await tester.pumpWidget(RepaintBoundary(key: key, child: const PatchApp()));
    await tester.runAsync(() async {
      for (final element in find.byType(Image).evaluate()) {
        await precacheImage((element.widget as Image).image, element);
      }
      final ctx = find.byType(Scaffold).evaluate().first;
      for (var i = 1; i <= 5; i++) {
        await precacheImage(AssetImage('img/game/cg$i.jpg'), ctx);
      }
      for (final s in ['btn_teal', 'btn_white', 'btn_coral']) {
        await precacheImage(AssetImage('img/game/$s.png'), ctx);
      }
    });
    await tester.pump(const Duration(seconds: 1));
    final boundary = key.currentContext!.findRenderObject() as RenderRepaintBoundary;
    final image = await tester.runAsync(() => boundary.toImage(pixelRatio: 1));
    final bytes = await tester.runAsync(() => image!.toByteData(format: ui.ImageByteFormat.png));
    File('build/android_preview.png').writeAsBytesSync(bytes!.buffer.asUint8List());
  });
}
