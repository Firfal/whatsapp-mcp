#!/bin/zsh
# Compile wa-snapshot et installe la tâche launchd qui le lance toutes les 5 minutes.
set -euo pipefail

LABEL="com.whatsapp-mcp.snapshot"
REPO="${0:A:h}"
BIN_DIR="$HOME/Library/Application Support/whatsapp-mcp"
BIN="$BIN_DIR/wa-snapshot"
LOG_DIR="$HOME/Library/Logs/whatsapp-mcp"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$BIN_DIR" "$LOG_DIR" "$HOME/Library/LaunchAgents"
swiftc -O "$REPO/helper/wa-snapshot.swift" -o "$BIN"
# Signature avec la première identité de signature valide du trousseau : l'accès complet au disque
# reste accordé après une recompilation. Sans identité, signature ad hoc : l'accès est à redonner
# à chaque compilation.
IDENTITY=$(security find-identity -v -p codesigning | awk -F'"' 'NR==1 && NF>2 {print $2}')
codesign --force --sign "${IDENTITY:--}" --identifier "$LABEL" "$BIN"
echo "Signé avec : ${IDENTITY:-ad hoc}"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key><array><string>$BIN</string></array>
    <key>StartInterval</key><integer>300</integer>
    <key>RunAtLoad</key><true/>
    <key>ProcessType</key><string>Background</string>
    <key>LowPriorityIO</key><true/>
    <key>StandardErrorPath</key><string>$LOG_DIR/snapshot.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"

echo "Installé. Ajoutez ce programme dans Réglages Système > Confidentialité et sécurité > Accès complet au disque :"
echo "  $BIN"
echo "(dans la fenêtre d'ajout, Cmd+Maj+G puis collez le chemin)"
