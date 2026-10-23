#!/usr/bin/env bash
# Sauvegarde complète de Lumistock : base MySQL/MariaDB, pièces justificatives
# (private/) et images (uploads/). Une archive datée par exécution, les plus
# anciennes sont supprimées après RETENTION_JOURS.
#
#   scripts/sauvegarde.sh                 # archive dans backend/sauvegardes/
#   DESTINATION=/mnt/disque scripts/sauvegarde.sh
#
# Tous les jours à 2 h (crontab -e) :
#   0 2 * * * cd /chemin/vers/backend && scripts/sauvegarde.sh >> sauvegardes/sauvegarde.log 2>&1
#
# Restauration : décompresser l'archive, puis
#   mysql -u UTILISATEUR -p BASE < base.sql
#   et recopier private/ et uploads/ dans le dossier backend.
set -euo pipefail

BACKEND="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BACKEND"

# Paramètres de connexion : variables d'environnement, sinon fichier .env
# (seules les lignes MYSQL_* sont lues : le reste du .env n'est pas exécuté).
if [ -f .env ]; then
  while IFS='=' read -r cle valeur; do
    valeur="${valeur%\"}"; valeur="${valeur#\"}"
    if [ -z "${!cle:-}" ]; then export "$cle=$valeur"; fi
  done < <(grep -E '^MYSQL_(HOST|PORT|USER|PASSWORD|DATABASE)=' .env || true)
fi
HOTE="${MYSQL_HOST:-127.0.0.1}"
PORT="${MYSQL_PORT:-3306}"
UTILISATEUR="${MYSQL_USER:-root}"
BASE="${MYSQL_DATABASE:-Lumistock_bd}"
DESTINATION="${DESTINATION:-$BACKEND/sauvegardes}"
RETENTION_JOURS="${RETENTION_JOURS:-30}"

HORODATAGE="$(date +%Y-%m-%d_%H%M)"
TRAVAIL="$(mktemp -d)"
trap 'rm -rf "$TRAVAIL"' EXIT
mkdir -p "$DESTINATION"

echo "[$(date '+%F %T')] Sauvegarde de la base $BASE…"
MYSQL_PWD="${MYSQL_PASSWORD:-}" mysqldump --host="$HOTE" --port="$PORT" --user="$UTILISATEUR" \
  --single-transaction --routines --triggers --default-character-set=utf8mb4 "$BASE" > "$TRAVAIL/base.sql"

ARCHIVE="$DESTINATION/lumistock_$HORODATAGE.tar.gz"
DOSSIERS=()
for d in private uploads; do
  [ -d "$d" ] && DOSSIERS+=("$d")
done
tar -czf "$ARCHIVE" -C "$TRAVAIL" base.sql -C "$BACKEND" "${DOSSIERS[@]}"
chmod 600 "$ARCHIVE"  # contient les données clients et les justificatifs

find "$DESTINATION" -name 'lumistock_*.tar.gz' -mtime +"$RETENTION_JOURS" -delete
echo "[$(date '+%F %T')] Archive créée : $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
