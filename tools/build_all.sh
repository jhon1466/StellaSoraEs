#!/usr/bin/env bash
# Full rebuild: retranslate every text source and package PC + Android.
# Usage: tools/build_all.sh <version>   (expects build/retranslate inputs from earlier runs)
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="$1"
N="E:/Documentos/SnowBreakPS/prueba server/Nebula"
W=build/retranslate
ORIGINAL_ARCX=$(ls -d C:/YostarGames/StellaSora_EN/StellaSoraES_backup/2* | head -1)/Persistent_Store/Tables/data.arcx
export PYTHONIOENCODING=utf-8

python tools/retranslate_opus.py json-tree "$N/resources/language/en_US" translations/language/es_ES_opus
python tools/retranslate_opus.py bubble "$N/resources/bubble/_en/BubbleData.json" translations/bubble/es_ES_opus/BubbleData.json
python tools/retranslate_opus.py srt $W/srt-en $W/srt-es
python tools/retranslate_opus.py catalog $W/catalog.json $W/ui-es.json
python tools/translate_lua_avg.py $W/lua/pc-lua.arcx "$N/com.YoStarEN.StellaSora/files/Persistent_Store/Scripts/lua.arcx" --output-dir $W/lua
python tools/patch_arcx_translations.py "$ORIGINAL_ARCX" "$N/resources/language/en_US" translations/language/es_ES_opus \
  "$N/resources/bubble/_en/BubbleData.json" translations/bubble/es_ES_opus/BubbleData.json "translations/data-arcx-es-$VERSION.arcx"
rm -rf $W/pc-ui $W/and-ui
python tools/translate_game_resources.py apply $W/pc-en $W/ui-es.json $W/pc-ui
python tools/translate_game_resources.py apply "$N/com.YoStarEN.StellaSora/files/Persistent_Store/AssetBundles" $W/ui-es.json $W/and-ui
sed -i "s/^VERSION = \".*\"/VERSION = \"$VERSION\"/" tools/prepare_patch.py
python tools/prepare_patch.py
python tools/build_patch_zip.py "translations/patch-source-full-v$VERSION.json" "dist/stella-sora-es-android-$VERSION.zip"
echo "BUILD_ALL_OK $VERSION"
