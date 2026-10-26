# whatsapp-mcp

Serveur MCP local, **en lecture seule**, qui permet à Claude (Claude Code, Claude Desktop…) de rechercher, lire et résumer vos conversations WhatsApp, et de repérer les messages restés sans réponse.

Il lit la base SQLite que **WhatsApp Desktop pour macOS** garde sur le disque. Il ne se connecte pas à WhatsApp et ne fait aucune requête réseau. Il ne peut pas envoyer de message : vous ne risquez donc pas de bannissement, contrairement aux projets qui utilisent l'API non officielle (whatsmeow, Baileys).

## Outils

| Outil | Rôle |
|---|---|
| `list_chats` | Discussions triées par date du dernier message, avec les non-lus et un aperçu du dernier message |
| `get_messages` | Messages d'une discussion dans l'ordre chronologique, avec filtre par dates (pour les résumés) |
| `search_messages` | Recherche de texte, avec 2 messages de contexte avant et après chaque résultat |
| `pending_replies` | Discussions dont le dernier message vient de l'autre personne et attend une réponse depuis N heures |
| `chat_activity` | Premier échange, dernier message de chaque côté, volume sur une période |
| `transcribe_voice_notes` | Transcrit les vocaux en local avec whisper ; le texte apparaît ensuite dans les autres outils |

On peut désigner une discussion par son nom (ou une partie unique du nom), par son JID ou par un numéro écrit librement (`06 12…`, `+33 6 12…`).

## Accès au disque limité au strict nécessaire

macOS protège le dossier de WhatsApp. Plutôt que de donner l'accès complet au disque à votre terminal (et donc à tout ce qui s'y exécute), le projet compile un petit programme, `wa-snapshot`, qui fait une seule chose : copier la base en lecture seule vers `~/Library/Caches/whatsapp-mcp/snapshot.sqlite` (lisible par vous seul).

- Seul `wa-snapshot` reçoit l'accès complet au disque.
- Il est lancé par launchd, toutes les 5 minutes ou à la demande du serveur. S'il était lancé par le terminal, macOS attribuerait l'accès au terminal et le refuserait.
- Le serveur MCP ne lit que la copie.
- Il copie aussi, à la demande du serveur, les vocaux à transcrire (uniquement des `.opus` du dossier `Media/` de WhatsApp) vers `~/Library/Caches/whatsapp-mcp/media/`.
- `install.sh` le signe avec la première identité de signature de votre trousseau, pour que l'accès survive aux recompilations. Sans identité, il est signé « ad hoc » et l'accès est à redonner après chaque compilation.

## Installation

Il faut macOS, WhatsApp Desktop associé à votre téléphone, [uv](https://docs.astral.sh/uv/) et les outils en ligne de commande Xcode (pour `swiftc`). Pour les vocaux : `ffmpeg` (`brew install ffmpeg`) et whisper (`pip install openai-whisper`).

```bash
git clone https://github.com/Firfal/whatsapp-mcp.git
cd whatsapp-mcp
./install.sh
```

Ensuite :

1. Réglages Système > Confidentialité et sécurité > Accès complet au disque : cliquez sur +, appuyez sur Cmd+Maj+G et collez `~/Library/Application Support/whatsapp-mcp/wa-snapshot`.
2. Ajoutez le serveur à Claude Code :
   ```bash
   claude mcp add whatsapp -s user -- uv run --directory "$PWD" server.py
   ```
   Pour Claude Desktop, ajoutez dans `claude_desktop_config.json` une entrée `"whatsapp"` avec `"command": "uv"` et `"args": ["run", "--directory", "/chemin/vers/whatsapp-mcp", "server.py"]`.
3. Ouvrez une nouvelle session et demandez par exemple : « Qui attend une réponse de ma part depuis plus de 2 jours ? »

Le journal de `wa-snapshot` est dans `~/Library/Logs/whatsapp-mcp/snapshot.log`.

## Vocaux

`transcribe_voice_notes` transcrit les vocaux d'une discussion, d'une période ou une liste de messages précise. Tout se passe sur votre Mac avec [whisper](https://github.com/openai/whisper) : l'audio n'est envoyé nulle part, seul le texte est transmis à Claude. Chaque vocal n'est transcrit qu'une fois. Le texte est gardé dans `~/Library/Caches/whatsapp-mcp/transcripts/`, puis s'affiche dans `get_messages` et est trouvé par `search_messages`.

Seuls les vocaux téléchargés par WhatsApp Desktop sont disponibles. Pour un ancien vocal, ouvrez-le dans l'app puis réessayez.

Modèle et langue se règlent dans `config.toml` (par défaut `small`, langue détectée automatiquement) :

```toml
whisper_model = "small"      # tiny, base, small, medium, turbo…
whisper_language = "fr"      # facultatif
```

## Exclure des discussions

Tout message lu par Claude est envoyé au fournisseur du modèle. Pour qu'une discussion ne soit jamais transmise, pas même son nom, créez `~/.config/whatsapp-mcp/config.toml` :

```toml
# Nom affiché (sans tenir compte de la casse) ou JID
exclude_chats = ["Maman♥️", "33612345678@s.whatsapp.net"]

# Si cette liste n'est pas vide, seules ces discussions sont visibles
only_chats = []
```

Le filtre s'applique à tous les outils. Il est pris en compte au prochain démarrage du serveur.

## Limites

- L'historique se limite à ce que WhatsApp Desktop a synchronisé.
- Le format de la base n'est pas documenté et peut changer avec une mise à jour de WhatsApp. Le serveur vérifie les colonnes au démarrage et indique clairement ce qui manque.
- Certains types de message ne sont pas identifiés. Ils s'affichent comme `[autre contenu, type N]`.
- Les autres pièces jointes (images, vidéos, documents) ne sont pas lues : seuls leur type et leur légende apparaissent.
- macOS uniquement.

## Désinstallation

```bash
launchctl bootout gui/$UID/com.whatsapp-mcp.snapshot
rm ~/Library/LaunchAgents/com.whatsapp-mcp.snapshot.plist
rm -r ~/Library/Application\ Support/whatsapp-mcp ~/Library/Caches/whatsapp-mcp ~/Library/Logs/whatsapp-mcp
claude mcp remove whatsapp -s user
```

Retirez ensuite `wa-snapshot` de la liste Accès complet au disque.

## Tests

```bash
uv run --group dev pytest
```

Les tests utilisent une fausse base qui reprend le schéma réel. Ils ne touchent jamais à vos données.
