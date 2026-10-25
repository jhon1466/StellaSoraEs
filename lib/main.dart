import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:archive/archive.dart';
import 'package:archive/archive_io.dart';
import 'package:crypto/crypto.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:shizuku_api/shizuku_api.dart';

const game = 'com.YoStarEN.StellaSora';
const versionsPath = 'file_versions.dat';
const maniPath = 'Persistent_Store/ss_and.mani';
void main() => runApp(const PatchApp());

class PatchApp extends StatelessWidget {
  const PatchApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'Stella Sora ES',
    theme: ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      fontFamily: 'MiSans',
      colorSchemeSeed: const Color(0xff2fd8de),
      scaffoldBackgroundColor: const Color(0xff070b1a),
      cardTheme: CardThemeData(
        color: const Color(0xff141b2e),
        elevation: 0,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(22)),
      ),
      snackBarTheme: const SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
      ),
    ),
    home: const PatchPage(),
  );
}

class PatchPage extends StatefulWidget {
  const PatchPage({super.key});
  @override
  State<PatchPage> createState() => _PatchPageState();
}

class _PatchPageState extends State<PatchPage>
    with SingleTickerProviderStateMixin {
  final api = ShizukuApi();
  final root = TextEditingController(
    text: '/storage/emulated/0/Android/data/$game/files',
  );
  bool busy = false, writable = false;
  double progress = 0;
  int? shizukuUid;
  String status = 'Inicia Shizuku y comprueba la carpeta de datos.';
  String client = '', resource = '';
  List<Map<String, String>> files = [];
  String? lastBackup, lastRoot;
  List<String> lastPaths = [];

  @override
  void initState() {
    super.initState();
    loadRestoreRecord();
  }

  String quote(String s) => "'${s.replaceAll("'", "'\"'\"'")}'";
  List<List<T>> batches<T>(List<T> items, {int size = 4}) => [
    for (var i = 0; i < items.length; i += size)
      items.sublist(i, i + size > items.length ? items.length : i + size),
  ];

  Future<void> saveRestoreRecord(
    String backup,
    String gameRoot,
    List<String> paths,
  ) async {
    lastBackup = backup;
    lastRoot = gameRoot;
    lastPaths = paths;
    final support = await getApplicationSupportDirectory();
    await File('${support.path}/last_patch.json').writeAsString(
      jsonEncode({'root': gameRoot, 'backup': backup, 'paths': paths}),
    );
  }

  String restoreBatchCommand(
    String backup,
    String gameRoot,
    List<String> paths,
  ) {
    final script = StringBuffer('set -eu\n');
    for (final path in paths) {
      script.writeln(
        'test -f ${quote('$backup/$path')} || { echo "Respaldo ausente"; exit 1; }',
      );
    }
    for (final path in paths) {
      final dst = '$gameRoot/$path',
          save = '$backup/$path',
          tmp = '$dst.stella-es-restore';
      script.writeln(
        'cp -p ${quote(save)} ${quote(tmp)} && mv -f ${quote(tmp)} ${quote(dst)}',
      );
    }
    script.writeln('echo __RESTORE_BATCH_OK__');
    return script.toString();
  }

  String backupBatchCommand(
    String backup,
    String gameRoot,
    List<Map<String, String>> batch,
  ) {
    final script = StringBuffer('set -eu\n');
    for (final f in batch) {
      final path = f['path']!;
      final dst = '$gameRoot/$path';
      final src = f['source']!;
      script.writeln(
        'test -f ${quote(dst)} || { echo "Falta destino: $path"; exit 1; }',
      );
      script.writeln(
        'test -r ${quote(src)} || { echo "Payload inaccesible: $path"; exit 1; }',
      );
      final expected = f['originalHash'];
      if (expected != null) {
        script.writeln(
          'got=\$(sha256sum ${quote(dst)} | cut -d " " -f1); if [ "\$got" != ${quote(expected)} ]; then echo ${quote('__BASE_HASH_MISMATCH__:$path')}; fi',
        );
      }
      final save = '$backup/$path';
      script.writeln('mkdir -p ${quote(Directory(save).parent.path)}');
      script.writeln('cp -p ${quote(dst)} ${quote(save)}');
    }
    script.writeln('echo __BACKUP_BATCH_OK__');
    return script.toString();
  }

  String installBatchCommand(String gameRoot, List<Map<String, String>> batch) {
    final script = StringBuffer('set -eu\n');
    for (final f in batch) {
      final dst = '$gameRoot/${f['path']!}';
      final src = f['source']!;
      final tmp = '$dst.stella-es-tmp';
      script.writeln('cp ${quote(src)} ${quote(tmp)}');
      script.writeln(
        'mode=\$(stat -c %a ${quote(dst)}); chmod "\$mode" ${quote(tmp)}',
      );
      script.writeln(
        'if [ "\$(id -u)" = 0 ]; then chown --reference=${quote(dst)} ${quote(tmp)}; fi',
      );
      script.writeln(
        'got=\$(sha256sum ${quote(tmp)} | cut -d " " -f1); [ "\$got" = ${quote(f['hash']!)} ]',
      );
      script.writeln('mv -f ${quote(tmp)} ${quote(dst)}');
      script.writeln(
        'got=\$(sha256sum ${quote(dst)} | cut -d " " -f1); [ "\$got" = ${quote(f['hash']!)} ]',
      );
    }
    script.writeln('echo __PATCH_BATCH_OK__');
    return script.toString();
  }

  String mergeVersionsCommand(String gameRoot, String updatesPath) {
    final dst = '$gameRoot/file_versions.dat';
    final tmp = '$dst.stella-es-tmp';
    final script = StringBuffer('set -eu\n');
    script.writeln(
      'test -r ${quote(dst)} || { echo "No se puede leer file_versions.dat"; exit 1; }',
    );
    script.writeln(
      'test -r ${quote(updatesPath)} || { echo "Falta el mapa de versiones"; exit 1; }',
    );
    script.writeln('''awk -F '|' -v map=${quote(updatesPath)} '
BEGIN {
  while ((getline row < map) > 0) {
    count = split(row, fields, "\\t")
    if (count != 2) exit 2
    updates[tolower(fields[1])] = fields[2]
  }
  close(map)
}
{
  key = \$1
  sub(/^\\/+/, "", key)
  gsub(/\\\\/, "/", key)
  key = tolower(key)
  if (key in updates) {
    print \$1 "|" updates[key]
    seen[key] = 1
  } else {
    print \$0
  }
}
END {
  for (key in updates) {
    if (!(key in seen)) {
      print "No existe entrada de versión: " key > "/dev/stderr"
      exit 3
    }
  }
}' ${quote(dst)} > ${quote(tmp)}''');
    script.writeln(
      'mode=\$(stat -c %a ${quote(dst)}); chmod "\$mode" ${quote(tmp)}',
    );
    script.writeln(
      'if [ "\$(id -u)" = 0 ]; then chown --reference=${quote(dst)} ${quote(tmp)}; fi',
    );
    script.writeln('mv -f ${quote(tmp)} ${quote(dst)}');
    script.writeln('echo __VERSIONS_MERGED_OK__');
    return script.toString();
  }

  /// Rewrites `name|md5|size|crc|` lines of ss_and.mani for patched files.
  /// An empty crc in the map keeps the original tag (e.g. @TAB, @VID).
  String mergeManiCommand(String gameRoot, String updatesPath) {
    final dst = '$gameRoot/$maniPath';
    final tmp = '$dst.stella-es-tmp';
    final script = StringBuffer('set -eu\n');
    script.writeln('''awk -F '|' -v OFS='|' -v map=${quote(updatesPath)} '
BEGIN {
  while ((getline row < map) > 0) {
    if (split(row, f, "\\t") != 4) exit 2
    md5[f[1]] = f[2]; size[f[1]] = f[3]; crc[f[1]] = f[4]
  }
  close(map)
}
(\$1 in md5) && NF >= 4 {
  \$2 = md5[\$1]; \$3 = size[\$1]
  if (crc[\$1] != "") \$4 = crc[\$1]
  seen[\$1] = 1
}
{ print }
END {
  for (k in md5) if (!(k in seen)) { print "No existe en ss_and.mani: " k > "/dev/stderr"; exit 3 }
}' ${quote(dst)} > ${quote(tmp)}''');
    script.writeln(
      'mode=\$(stat -c %a ${quote(dst)}); chmod "\$mode" ${quote(tmp)}',
    );
    script.writeln(
      'if [ "\$(id -u)" = 0 ]; then chown --reference=${quote(dst)} ${quote(tmp)}; fi',
    );
    script.writeln('mv -f ${quote(tmp)} ${quote(dst)}');
    script.writeln('echo __MANI_MERGED_OK__');
    return script.toString();
  }

  bool _validRoot(String path) =>
      path.startsWith('/') &&
      !path.contains(r'\') &&
      path.endsWith('/$game/files') &&
      !path.split('/').any((part) => part == '..' || part == '.');
  void message(String s) {
    if (mounted) setState(() => status = s);
  }

  Future<String> command(String s) async => await api.runCommand(s) ?? '';
  Future<void> check() async {
    setState(() {
      busy = true;
      status = 'Comprobando Shizuku…';
    });
    try {
      if (!(await api.pingBinder() ?? false)) {
        message('Shizuku no está iniciado.');
        return;
      }
      var allowed = await api.checkPermission() ?? false;
      if (!allowed) allowed = await api.requestPermission() ?? false;
      if (!allowed) {
        message('No se concedió permiso de Shizuku.');
        return;
      }
      final path = root.text.trim();
      if (!_validRoot(path)) {
        message('La ruta debe acabar en /$game/files.');
        return;
      }
      final q = quote(path);
      final out = await command('''
uid=\$(id -u 2>/dev/null || echo -1)
echo "UID=\$uid"
if [ ! -d $q/Persistent_Store ] || [ ! -f $q/Persistent_Store/ss_and.mani ]; then echo NOT_FOUND; exit 0; fi
echo FOUND
echo "CLIENT=\$(sed -n 's/^\\\$CLIENT_VER://p' $q/Persistent_Store/ss_and.mani | head -n 1)"
echo "RESOURCE=\$(sed -n 's/^\\\$RES_VER://p' $q/Persistent_Store/ss_and.mani | head -n 1)"
probe=$q/Persistent_Store/.stella-sora-es-probe-\$\$
if (umask 077; : > "\$probe") 2>/dev/null; then
  if rm -f "\$probe"; then echo WRITE_PROBE=1; else echo WRITE_PROBE=0; fi
else
  echo WRITE_PROBE=0
fi
''');
      shizukuUid = int.tryParse(
        RegExp(r'UID=(\d+)').firstMatch(out)?.group(1) ?? '',
      );
      if (!out.contains('FOUND')) {
        writable = false;
        message(
          'UID ${shizukuUid ?? '?'} no ve la carpeta. Prueba /storage/emulated/0/Android/data/$game/files; si está en /data/user/0, puede hacer falta root/Sui.',
        );
        return;
      }
      client = RegExp(r'CLIENT=([^\r\n]*)').firstMatch(out)?.group(1) ?? '';
      resource = RegExp(r'RESOURCE=([^\r\n]*)').firstMatch(out)?.group(1) ?? '';
      writable = out.contains('WRITE_PROBE=1');
      message(
        writable
            ? 'Detectado · UID $shizukuUid · cliente $client · recursos $resource · escritura confirmada.'
            : shizukuUid == 2000
            ? 'UID 2000 (shell) detecta los datos, pero Android no le permite escribir ahí. Para /data/user/0 necesitas Shizuku root/Sui o una ruta externa escribible.'
            : shizukuUid == 0
            ? 'UID 0 (root) detecta los datos, pero falló la prueba de escritura. Puede bloquearlo SELinux o estar montado de solo lectura.'
            : 'Datos detectados por UID $shizukuUid, pero falló la prueba real de escritura.',
      );
    } catch (e) {
      message('Error Shizuku: $e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  bool safe(String p) {
    if (p.startsWith('/') ||
        p.contains(r'\') ||
        p.contains('\t') ||
        p.contains('\n') ||
        p.contains('\r') ||
        p.split('/').any((x) => x == '..' || x == '.' || x.isEmpty)) {
      return false;
    }
    return p.startsWith('Persistent_Store/') || p == 'file_versions.dat';
  }

  Future<void> chooseZip() async {
    InputFileStream? zipInput;
    try {
      final pick = await FilePicker.pickFile(
        type: FileType.custom,
        allowedExtensions: const ['zip'],
      );
      if (pick == null) return;
      final path = pick.path;
      if (path == null) throw Exception('No se recibió la ruta del ZIP.');
      setState(() {
        busy = true;
        status = 'Validando paquete…';
      });
      zipInput = InputFileStream(path);
      final archive = ZipDecoder().decodeStream(zipInput);
      final manifests = archive.files
          .where((f) => f.name == 'manifest.json' && f.isFile)
          .toList();
      if (manifests.length != 1) {
        throw Exception('El ZIP requiere manifest.json único en la raíz.');
      }
      final json =
          jsonDecode(utf8.decode(manifests.single.content))
              as Map<String, dynamic>;
      if (json['format'] != 'stella-sora-es-patch' ||
          json['schemaVersion'] != 1 ||
          json['gamePackage'] != game ||
          json['platform'] != 'android') {
        throw Exception('Paquete Android incompatible.');
      }
      final version = json['clientVersion'];
      if (version is! String ||
          json['patchVersion'] is! String ||
          json['files'] is! List ||
          (json['files'] as List).isEmpty ||
          (json['files'] as List).length > 20000) {
        throw Exception('Manifiesto incompleto.');
      }
      if (json['resourceVersion'] != null &&
          json['resourceVersion'] is! String) {
        throw Exception('resourceVersion debe ser texto.');
      }
      final ext = await getExternalStorageDirectory();
      if (ext == null) {
        throw Exception('No hay carpeta externa para preparar archivos.');
      }
      final stage = Directory('${ext.path}/stella_sora_es_patch/staging');
      await stage.create(recursive: true);
      final listed = <Map<String, String>>[];
      final seenPaths = <String>{};
      var totalBytes = 0;
      for (final raw in json['files'] as List) {
        final e = raw as Map<String, dynamic>;
        final dest = e['path'] as String, hash = e['sha256'] as String;
        if (!safe(dest) ||
            !seenPaths.add(dest) ||
            !RegExp(r'^[0-9a-fA-F]{64}$').hasMatch(hash)) {
          throw Exception('Ruta o hash inválido.');
        }
        final payload = archive.files
            .where((f) => f.name == 'payload/$dest' && f.isFile)
            .toList();
        if (payload.length != 1) throw Exception('Falta payload/$dest');
        if (payload.single.isSymbolicLink) {
          throw Exception('El payload no puede ser un enlace: $dest');
        }
        totalBytes += payload.single.size;
        if (totalBytes > 20 * 1024 * 1024 * 1024) {
          throw Exception('El paquete supera 20 GiB.');
        }
        final originalHash = e['originalSha256'];
        if (originalHash != null &&
            (originalHash is! String ||
                !RegExp(r'^[0-9a-fA-F]{64}$').hasMatch(originalHash))) {
          throw Exception('Hash de origen inválido: $dest');
        }
        final output = File('${stage.path}/$dest');
        await output.parent.create(recursive: true);
        final outputStream = OutputFileStream(output.path);
        try {
          payload.single.writeContent(outputStream);
        } finally {
          await outputStream.close();
        }
        final actualHash = (await sha256.bind(output.openRead()).first)
            .toString();
        if (actualHash.toLowerCase() != hash.toLowerCase()) {
          throw Exception('Hash incorrecto: $dest');
        }
        listed.add({
          'path': dest,
          'hash': hash.toLowerCase(),
          'source': output.path,
          if (originalHash != null) 'originalHash': originalHash.toLowerCase(),
        });
      }
      // The game checks MD5 in file_versions.dat and MD5/size/CRC in
      // ss_and.mani; the package carries them so only those lines are edited.
      for (final raw in json['files'] as List) {
        final e = raw as Map<String, dynamic>;
        final file = listed.firstWhere((f) => f['path'] == e['path']);
        final md5 = e['md5'], size = e['size'], crc = e['crc'];
        if (md5 is! String ||
            !RegExp(r'^[0-9a-f]{32}$').hasMatch(md5) ||
            size is! int ||
            (crc != null && crc is! int)) {
          throw Exception('Paquete antiguo o sin datos de mani: ${e['path']}');
        }
        file['versionHash'] = md5;
        file['mani'] = '$md5|$size|${crc ?? ''}';
      }
      if (version != client) {
        throw Exception(
          'El parche es para cliente $version; el juego tiene $client.',
        );
      }
      setState(() {
        files = listed;
      });
      message(
        'Paquete ${json['patchVersion']?.toString() ?? ''} verificado: ${files.length} archivos.',
      );
    } catch (e) {
      message('No se pudo cargar: $e');
    } finally {
      await zipInput?.close();
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> install() async {
    if (!writable || files.isEmpty) return;
    setState(() {
      busy = true;
      status = 'Respaldando e instalando…';
    });
    try {
      if (!(await api.pingBinder() ?? false)) {
        throw Exception('Shizuku se desconectó.');
      }
      if (!(await api.checkPermission() ?? false)) {
        throw Exception('Permiso Shizuku no concedido.');
      }
      final ext = await getExternalStorageDirectory();
      if (ext == null) throw Exception('Sin almacenamiento externo.');
      final gameRoot = root.text.trim();
      final paths = [...files.map((f) => f['path']!), versionsPath, maniPath];
      final backup =
          '${ext.path}/stella_sora_es_patch/backups/${DateTime.now().millisecondsSinceEpoch}';
      final meta = StringBuffer('set -eu\nmkdir -p ${quote(backup)}\n');
      for (final path in [versionsPath, maniPath]) {
        final save = '$backup/$path';
        meta.writeln('mkdir -p ${quote(Directory(save).parent.path)}');
        meta.writeln('cp -p ${quote('$gameRoot/$path')} ${quote(save)}');
      }
      meta.writeln('echo __BACKUP_ROOT_OK__');
      final init = await command(meta.toString());
      if (!init.contains('__BACKUP_ROOT_OK__')) throw Exception(init);

      final gameFileBatches = batches(files);
      final baseMismatches = <String>[];
      var completed = 0;
      for (final batch in gameFileBatches) {
        progress = .5 * completed / files.length;
        message('Respaldando ${completed + batch.length}/${files.length}…');
        final out = await command(backupBatchCommand(backup, gameRoot, batch));
        if (!out.contains('__BACKUP_BATCH_OK__')) throw Exception(out);
        for (final line in const LineSplitter().convert(out)) {
          if (line.startsWith('__BASE_HASH_MISMATCH__:')) {
            baseMismatches.add(
              line.substring('__BASE_HASH_MISMATCH__:'.length),
            );
          }
        }
        completed += batch.length;
      }

      // Persist recovery information before the first game file is changed.
      await saveRestoreRecord(backup, gameRoot, paths);
      final touched = <String>[];
      try {
        completed = 0;
        for (final batch in gameFileBatches) {
          progress = .5 + .45 * completed / files.length;
          message('Instalando ${completed + batch.length}/${files.length}…');
          // Include this batch in rollback because a failed shell call may have
          // changed some files before returning an error.
          touched.addAll(batch.map((f) => f['path']!));
          final out = await command(installBatchCommand(gameRoot, batch));
          if (!out.contains('__PATCH_BATCH_OK__')) throw Exception(out);
          completed += batch.length;
        }
        final staging = '${ext.path}/stella_sora_es_patch/staging';
        final versionUpdates = File('$staging/version_updates.tsv');
        await versionUpdates.writeAsString(
          files.map((f) => '${f['path']!}\t${f['versionHash']!}').join('\n'),
        );
        final maniUpdates = File('$staging/mani_updates.tsv');
        await maniUpdates.writeAsString(
          files
              .map(
                (f) =>
                    '${f['path']!.split('/').last}\t${f['mani']!.replaceAll('|', '\t')}',
              )
              .join('\n'),
        );
        progress = .97;
        message('Actualizando file_versions.dat y ss_and.mani…');
        touched.addAll([versionsPath, maniPath]);
        final versionsOut = await command(
          mergeVersionsCommand(gameRoot, versionUpdates.path),
        );
        if (!versionsOut.contains('__VERSIONS_MERGED_OK__')) {
          throw Exception(versionsOut);
        }
        final maniOut = await command(
          mergeManiCommand(gameRoot, maniUpdates.path),
        );
        if (!maniOut.contains('__MANI_MERGED_OK__')) throw Exception(maniOut);
      } catch (installError) {
        Object? rollbackError;
        try {
          for (final batch in batches(touched)) {
            final out = await command(
              restoreBatchCommand(backup, gameRoot, batch),
            );
            if (!out.contains('__RESTORE_BATCH_OK__')) throw Exception(out);
          }
        } catch (e) {
          rollbackError = e;
        }
        if (rollbackError != null) {
          // Keep last_patch.json and the backup so restore can be retried.
          throw Exception(
            'Falló la instalación y la reversión. Respaldo disponible en $backup. Error: $installError. Reversión: $rollbackError',
          );
        }
        try {
          final support = await getApplicationSupportDirectory();
          final record = File('${support.path}/last_patch.json');
          if (await record.exists()) await record.delete();
        } catch (_) {
          // The restore record is useful only when it can be kept; rollback is
          // already complete, so report the original install error either way.
        }
        lastBackup = null;
        lastRoot = null;
        lastPaths = [];
        throw Exception(
          'Falló la instalación; los archivos originales se restauraron. $installError',
        );
      }
      if (mounted) setState(() {});
      message(
        baseMismatches.isEmpty
            ? 'Parche aplicado y verificado. Respaldo en $backup'
            : 'Parche aplicado con ${baseMismatches.length} archivo(s) base distinto(s); se respaldaron antes de reemplazarlos. Respaldo en $backup',
      );
    } catch (e) {
      message('No se completó: $e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> restore() async {
    if (lastBackup == null || lastRoot == null || lastPaths.isEmpty) return;
    setState(() {
      busy = true;
      status = 'Restaurando archivos originales…';
    });
    try {
      if (!(await api.pingBinder() ?? false)) {
        throw Exception('Shizuku se desconectó.');
      }
      if (!(await api.checkPermission() ?? false)) {
        throw Exception('Permiso Shizuku no concedido.');
      }
      var completed = 0;
      final restoreBatches = batches(lastPaths);
      for (final batch in restoreBatches) {
        message('Restaurando ${completed + batch.length}/${lastPaths.length}…');
        final out = await command(
          restoreBatchCommand(lastBackup!, lastRoot!, batch),
        );
        if (!out.contains('__RESTORE_BATCH_OK__')) throw Exception(out);
        completed += batch.length;
      }
      final support = await getApplicationSupportDirectory();
      await File('${support.path}/last_patch.json').delete();
      setState(() {
        lastBackup = null;
        lastRoot = null;
        lastPaths = [];
        files = [];
      });
      message('Archivos originales restaurados.');
    } catch (e) {
      message('No se pudo restaurar: $e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> loadRestoreRecord() async {
    try {
      final support = await getApplicationSupportDirectory();
      final record = File('${support.path}/last_patch.json');
      if (!await record.exists()) return;
      final json =
          jsonDecode(await record.readAsString()) as Map<String, dynamic>;
      final savedRoot = json['root'] as String?;
      final savedBackup = json['backup'] as String?;
      final savedPaths = (json['paths'] as List).cast<String>();
      final ext = await getExternalStorageDirectory();
      if (savedRoot == null ||
          !_validRoot(savedRoot) ||
          savedBackup == null ||
          ext == null ||
          !savedBackup.startsWith(
            '${ext.path}/stella_sora_es_patch/backups/',
          ) ||
          savedPaths.isEmpty ||
          savedPaths.any((path) => !safe(path))) {
        return;
      }
      if (!mounted) return;
      setState(() {
        lastRoot = savedRoot;
        lastBackup = savedBackup;
        lastPaths = savedPaths;
      });
    } catch (_) {}
  }

  // ── Diseño ────────────────────────────────────────────────────────────
  // Visual language of the game client: story CG backdrop, official logo, white
  // popup panels with a navy offset shadow, the client's own 9-slice button
  // sprites and the AVG dialogue box (name, star-tipped rule, text) for status.
  static const _ink = Color(0xff1f3348);
  static const _inkSoft = Color(0xff5b6b80);
  static const _teal = Color(0xff2fd8de);
  static const _coral = Color(0xffec4a6a);
  static const _line = Color(0xffd5dee8);
  static const _cg = [
    'img/game/cg1.jpg',
    'img/game/cg2.jpg',
    'img/game/cg3.jpg',
    'img/game/cg4.jpg',
    'img/game/cg5.jpg',
  ];

  late final AnimationController _pan = AnimationController(
    vsync: this,
    duration: const Duration(seconds: 24),
  )..repeat(reverse: true);
  Timer? _cgTimer;
  int _cgIndex = 0;

  void _startCg() {
    _cgTimer ??= Timer.periodic(const Duration(seconds: 8), (_) {
      if (mounted) setState(() => _cgIndex = (_cgIndex + 1) % _cg.length);
    });
  }

  bool get _isError =>
      status.startsWith('No ') ||
      status.startsWith('Error') ||
      status.contains('Falló');

  @override
  Widget build(BuildContext context) {
    _startCg();
    final connected = client.isNotEmpty;
    final patchLoaded = files.isNotEmpty;
    final canInstall = !busy && writable && patchLoaded;
    return Scaffold(
      backgroundColor: const Color(0xff0c1220),
      body: Stack(
        fit: StackFit.expand,
        children: [
          AnimatedSwitcher(
            duration: const Duration(milliseconds: 1800),
            child: AnimatedBuilder(
              key: ValueKey(_cgIndex),
              animation: _pan,
              builder: (_, child) => Transform.scale(
                scale: 1.12,
                child: Transform.translate(
                  offset: Offset(24 * (_pan.value - .5), 0),
                  child: child,
                ),
              ),
              child: Image.asset(
                _cg[_cgIndex],
                fit: BoxFit.cover,
                width: double.infinity,
                height: double.infinity,
              ),
            ),
          ),
          const DecoratedBox(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [
                  Color(0x33080c1a),
                  Color(0x00080c1a),
                  Color(0xcc0a1022),
                ],
                stops: [0, .35, 1],
              ),
            ),
          ),
          SafeArea(
            child: Column(
              children: [
                Expanded(
                  child: ListView(
                    padding: const EdgeInsets.fromLTRB(18, 14, 18, 8),
                    children: [
                      _logo(),
                      const SizedBox(height: 18),
                      _popup(
                        children: [
                          _sectionTitle('Paso 1 · Conectar Shizuku'),
                          const SizedBox(height: 10),
                          _infoBox(
                            title: connected
                                ? 'Cliente $client · recursos $resource'
                                : 'Inicia Shizuku y concede permiso',
                            badge: writable
                                ? 'Escritura OK'
                                : (connected ? 'Sin escritura' : null),
                            badgeOk: writable,
                            child: TextField(
                              controller: root,
                              style: const TextStyle(
                                fontSize: 11.5,
                                fontFamily: 'monospace',
                                color: _ink,
                              ),
                              decoration: const InputDecoration(
                                isDense: true,
                                border: InputBorder.none,
                                contentPadding: EdgeInsets.zero,
                              ),
                            ),
                          ),
                          const SizedBox(height: 10),
                          _spriteButton(
                            'btn_white',
                            writable ? 'Volver a comprobar' : 'Conectar',
                            busy ? null : check,
                            dark: true,
                          ),
                          const SizedBox(height: 20),
                          _sectionTitle('Paso 2 · Paquete del parche'),
                          const SizedBox(height: 10),
                          _infoBox(
                            title: patchLoaded
                                ? '${files.length} archivos verificados'
                                : 'stella-sora-es-android-0.2.8.zip',
                            badge: patchLoaded ? 'Listo' : null,
                            badgeOk: true,
                          ),
                          const SizedBox(height: 10),
                          _spriteButton(
                            'btn_white',
                            patchLoaded ? 'Cambiar ZIP' : 'Elegir ZIP',
                            busy || !connected ? null : chooseZip,
                            dark: true,
                          ),
                          const SizedBox(height: 20),
                          _sectionTitle('Paso 3 · Instalar'),
                          const SizedBox(height: 12),
                          _spriteButton(
                            'btn_teal',
                            busy
                                ? 'Instalando… ${(progress * 100).round()}%'
                                : 'Instalar en español',
                            canInstall ? install : null,
                            height: 60,
                            fontSize: 17,
                          ),
                          if (busy) ...[
                            const SizedBox(height: 10),
                            _progressBar(),
                          ],
                          if (lastPaths.isNotEmpty && !busy) ...[
                            const SizedBox(height: 10),
                            _spriteButton(
                              'btn_coral',
                              'Volver al inglés',
                              restore,
                              height: 44,
                              fontSize: 13,
                            ),
                          ],
                        ],
                      ),
                    ],
                  ),
                ),
                _avgBox(),
              ],
            ),
          ),
        ],
      ),
    );
  }

  @override
  void dispose() {
    _cgTimer?.cancel();
    _pan.dispose();
    root.dispose();
    super.dispose();
  }

  Widget _star(Color color, {double size = 11}) =>
      CustomPaint(size: Size.square(size), painter: _StarPainter(color));

  Widget _logo() => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Image.asset('img/game/logo.png', width: 230),
      Transform.translate(
        offset: const Offset(10, -8),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
          decoration: BoxDecoration(
            color: _ink.withValues(alpha: .85),
            borderRadius: BorderRadius.circular(3),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              _star(_teal),
              const SizedBox(width: 7),
              const Text(
                'EDICIÓN EN ESPAÑOL  ',
                style: TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 2,
                  color: Colors.white,
                ),
              ),
              const Text(
                'v0.2.8',
                style: TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.w700,
                  color: _teal,
                ),
              ),
            ],
          ),
        ),
      ),
    ],
  );

  Widget _popup({required List<Widget> children}) => Container(
    padding: const EdgeInsets.all(18),
    decoration: BoxDecoration(
      gradient: const LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [Colors.white, Color(0xffeef3f7)],
      ),
      borderRadius: BorderRadius.circular(14),
      border: Border.all(color: Colors.white, width: 2),
      boxShadow: const [
        BoxShadow(color: Color(0xd91f3348), offset: Offset(5, 6)),
        BoxShadow(
          color: Color(0x66000000),
          blurRadius: 30,
          offset: Offset(0, 14),
        ),
      ],
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: children,
    ),
  );

  Widget _sectionTitle(String text) => Row(
    children: [
      Container(
        width: 5,
        height: 16,
        decoration: BoxDecoration(
          color: _teal,
          borderRadius: BorderRadius.circular(2),
        ),
      ),
      const SizedBox(width: 8),
      Text(
        text,
        style: const TextStyle(
          color: _ink,
          fontSize: 14.5,
          fontWeight: FontWeight.w700,
        ),
      ),
      const SizedBox(width: 8),
      const Expanded(child: Divider(color: _line, height: 1)),
    ],
  );

  Widget _infoBox({
    required String title,
    String? badge,
    bool badgeOk = false,
    Widget? child,
  }) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
    decoration: BoxDecoration(
      color: Colors.white.withValues(alpha: .75),
      borderRadius: BorderRadius.circular(8),
      border: Border.all(color: _line),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                title,
                style: const TextStyle(
                  color: _inkSoft,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
            if (badge != null)
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: (badgeOk ? _teal : _coral).withValues(alpha: .18),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Text(
                  badge,
                  style: TextStyle(
                    fontSize: 10.5,
                    fontWeight: FontWeight.w700,
                    color: badgeOk ? const Color(0xff0f8f95) : _coral,
                  ),
                ),
              ),
          ],
        ),
        if (child != null) ...[const SizedBox(height: 6), child],
      ],
    ),
  );

  /// Button drawn with the client's 9-slice sprite (img/game/`sprite`.png, 191x58).
  Widget _spriteButton(
    String sprite,
    String label,
    VoidCallback? onTap, {
    double height = 48,
    double fontSize = 14,
    bool dark = false,
  }) => Opacity(
    opacity: onTap == null ? .5 : 1,
    child: GestureDetector(
      onTap: onTap,
      child: Container(
        height: height,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          image: DecorationImage(
            image: AssetImage('img/game/$sprite.png'),
            centerSlice: const Rect.fromLTRB(24, 20, 167, 38),
            fit: BoxFit.fill,
          ),
        ),
        padding: const EdgeInsets.only(bottom: 4),
        child: Text(
          label,
          style: TextStyle(
            color: dark ? _ink : Colors.white,
            fontSize: fontSize,
            fontWeight: FontWeight.w700,
            letterSpacing: .6,
            shadows: dark
                ? null
                : const [
                    Shadow(color: Color(0x59005a64), offset: Offset(0, 1)),
                  ],
          ),
        ),
      ),
    ),
  );

  Widget _progressBar() => LayoutBuilder(
    builder: (_, box) => SizedBox(
      height: 12,
      child: Stack(
        alignment: Alignment.centerLeft,
        children: [
          Container(
            height: 5,
            decoration: BoxDecoration(
              color: _line,
              borderRadius: BorderRadius.circular(3),
            ),
          ),
          AnimatedContainer(
            duration: const Duration(milliseconds: 300),
            height: 5,
            width: box.maxWidth * progress.clamp(0, 1),
            decoration: BoxDecoration(
              color: _teal,
              borderRadius: BorderRadius.circular(3),
            ),
          ),
          AnimatedPositioned(
            duration: const Duration(milliseconds: 300),
            left: (box.maxWidth * progress.clamp(0, 1) - 6).clamp(
              0,
              box.maxWidth - 12,
            ),
            child: _star(_teal, size: 12),
          ),
        ],
      ),
    ),
  );

  Widget _avgBox() => Container(
    width: double.infinity,
    padding: const EdgeInsets.fromLTRB(26, 18, 26, 20),
    decoration: const BoxDecoration(
      gradient: LinearGradient(
        begin: Alignment.topCenter,
        end: Alignment.bottomCenter,
        colors: [Color(0x000a1022), Color(0xd90a1022), Color(0xf00a1022)],
        stops: [0, .3, 1],
      ),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          'Stella Sora ES',
          style: TextStyle(
            color: Colors.white,
            fontSize: 15,
            fontWeight: FontWeight.w600,
            shadows: [Shadow(blurRadius: 3)],
          ),
        ),
        const SizedBox(height: 6),
        Row(
          children: [
            _star(Colors.white70, size: 10),
            Expanded(
              child: Container(
                height: 1,
                decoration: const BoxDecoration(
                  gradient: LinearGradient(
                    colors: [
                      Color(0x26ffffff),
                      Color(0x8cffffff),
                      Color(0x8cffffff),
                      Color(0x26ffffff),
                    ],
                    stops: [0, .08, .92, 1],
                  ),
                ),
              ),
            ),
            _star(Colors.white70, size: 10),
          ],
        ),
        const SizedBox(height: 10),
        Text(
          status,
          style: TextStyle(
            color: _isError ? const Color(0xffffd2dc) : Colors.white,
            fontSize: 15.5,
            height: 1.4,
            shadows: const [Shadow(blurRadius: 3)],
          ),
        ),
      ],
    ),
  );
}

/// Four-point star used by the game's dialogue rule and selection marks.
class _StarPainter extends CustomPainter {
  const _StarPainter(this.color);
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width, h = size.height;
    final path = Path()
      ..moveTo(w / 2, 0)
      ..lineTo(w * .6, h * .4)
      ..lineTo(w, h / 2)
      ..lineTo(w * .6, h * .6)
      ..lineTo(w / 2, h)
      ..lineTo(w * .4, h * .6)
      ..lineTo(0, h / 2)
      ..lineTo(w * .4, h * .4)
      ..close();
    canvas.drawPath(path, Paint()..color = color);
  }

  @override
  bool shouldRepaint(_StarPainter old) => old.color != color;
}
