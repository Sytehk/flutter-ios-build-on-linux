import 'dart:io';

import 'package:flutter_tools/src/cache.dart';
import 'package:flutter_tools/src/base/template.dart';
import 'package:flutter_tools/src/context_runner.dart';
import 'package:flutter_tools/src/darwin/darwin.dart';
import 'package:flutter_tools/src/flutter_plugins.dart';
import 'package:flutter_tools/src/globals.dart' as globals;
import 'package:flutter_tools/src/isolated/mustache_template.dart';
import 'package:flutter_tools/src/macos/swift_package_manager.dart';
import 'package:flutter_tools/src/project.dart';

Future<void> main() {
  Cache.flutterRoot = Platform.environment['FLUTTER_ROOT']!;
  return runInContext(() async {
    final project = FlutterProject.current();
    await refreshPluginsList(project, iosPlatform: true, forceSwiftPM: true);
    final plugins = resolvePluginImplementationsForPlatform(
      await findPlugins(project, logger: globals.logger),
      'ios',
    );
    await writeIOSPluginRegistrant(project, plugins);
    await SwiftPackageManager(
      fileSystem: globals.fs,
      templateRenderer: globals.templateRenderer,
      processUtils: globals.processUtils,
      config: globals.config,
      logger: globals.logger,
    ).generatePluginsSwiftPackage(
      plugins,
      FlutterDarwinPlatform.ios,
      project.ios,
    );
  }, overrides: {TemplateRenderer: () => const MustacheTemplateRenderer()});
}
