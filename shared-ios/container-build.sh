#!/usr/bin/env bash
set -euo pipefail

ln -sf /usr/bin/llvm-strip-14 /usr/bin/strip
ln -s /usr/bin/llvm-otool-14 /usr/local/bin/otool
ln -s /usr/bin/llvm-install-name-tool-14 /usr/local/bin/install_name_tool
unset SWIFT_VERSION
ulimit -Sn "$(ulimit -Hn)"
export PATH="$SWEETPAD_SHARED_TOOLS/apple-shims:$FLUTTER_ROOT/bin:$PATH"
# permission_handler cannot find Info.plist by scanning the source mounted from
# the Windows drive, so it would compile every permission out.
export PERMISSION_HANDLER_INFO_PLIST="${PERMISSION_HANDLER_INFO_PLIST:-$PWD/ios/Runner/Info.plist}"

if [[ -d $SWEETPAD_PROJECT_STATE/git-mirrors/libwebp.git ]]; then
  cp -R "$SWEETPAD_PROJECT_STATE/git-mirrors/libwebp.git" /tmp/sweetpad-libwebp.git
  git config --global url.file:///tmp/sweetpad-libwebp.git.insteadOf https://github.com/webmproject/libwebp
fi
git config --global protocol.file.allow always
git config --global --add safe.directory "$FLUTTER_ROOT"
for repo in /root/.cache/org.swift.swiftpm/repositories/*; do
  [[ -d $repo ]] || continue
  url=$(git --git-dir="$repo" config --local --get remote.origin.url || true)
  case "$url" in
    https://github.com/*)
      base=${url%/}
      base=${base%.git}
      for variant in "$base" "$base.git" "$base/" "$base.git/"; do
        git config --global --add "url.file://$repo.insteadOf" "$variant"
      done
      ;;
  esac
done

if [[ ${1:-} == prepare ]]; then
  flutter pub get --offline --enforce-lockfile --no-example
  "$FLUTTER_ROOT/bin/cache/dart-sdk/bin/dart" \
    --packages="$FLUTTER_ROOT/packages/flutter_tools/.dart_tool/package_config.json" \
    "$SWEETPAD_SHARED_TOOLS/prepare_ios_plugins.dart"
  chown "$SWEETPAD_HOST_UID:$SWEETPAD_HOST_GID" \
    ios/Flutter/ephemeral/Packages/FlutterGeneratedPluginSwiftPackage/Package.swift
  exit
fi

sweetpad build --workspace "$1" --scheme "$2" --configuration "$3" \
  --destination 'generic/platform=iOS' --non-interactive -- \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO TARGET_DEVICE_OS_VERSION=27.2 \
  DEPLOYMENT_POSTPROCESSING=NO STRIP_INSTALLED_PRODUCT=NO COPY_PHASE_STRIP=NO "${@:4}"

shopt -s nullglob
for app in /root/Library/Developer/Xcode/DerivedData/*/Build/Products/"$3-iphoneos"/*.app; do
  chown -R "$SWEETPAD_HOST_UID:$SWEETPAD_HOST_GID" -- "$app"
done
