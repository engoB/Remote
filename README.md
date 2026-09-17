# Offres en télétravail pour francophones (projet perso)

Une page statique hébergée sur GitHub Pages. Une GitHub Action récupère les offres toutes les 6 heures,
les enregistre dans `data/jobs.json`, et la page les affiche avec des filtres.
Chaque offre renvoie vers **l'annonce d'origine** et indique sa source.

## Mise en route (15 minutes)

1. **Crée un dépôt** sur GitHub (par exemple `offres-remote-fr`) et envoie-y tous ces fichiers,
   y compris le dossier caché `.github` et le fichier `.nojekyll`.
2. **Autorise l'Action à écrire** : *Settings → Actions → General → Workflow permissions* →
   « Read and write permissions ».
3. **Active GitHub Pages** : *Settings → Pages* → Source « Deploy from a branch », branche `main`, dossier `/ (root)`.
   Ta page sera à l'adresse `https://TON-PSEUDO.github.io/offres-remote-fr/`.
4. **Lance une première collecte** : onglet *Actions* → « Mise à jour des offres » → *Run workflow*.
   Au bout d'une à deux minutes, recharge ta page.

À ce stade tu as déjà les offres Remotive. Ajoute ensuite les autres sources.

## Ajouter France Travail (recommandé)

1. Crée un compte sur https://francetravail.io, crée une application et abonne-la à l'API « Offres d'emploi ».
2. Récupère l'identifiant client et la clé secrète.
3. Dans le dépôt : *Settings → Secrets and variables → Actions → New repository secret*,
   crée `FT_CLIENT_ID` et `FT_CLIENT_SECRET`.

Les clés restent dans GitHub : elles ne sont jamais visibles sur la page.
Si la collecte France Travail affiche une erreur dans « Détail de la dernière collecte », compare l'URL
d'authentification et le `scope` de `fetch.py` avec la documentation actuelle de francetravail.io.

## Ajouter des entreprises

Sur le site carrière d'une entreprise qui recrute à distance, clique sur « Postuler » et regarde l'URL :

| URL | À ajouter dans `companies.json` |
|---|---|
| `jobs.lever.co/exemple` | `"lever": ["exemple"]` |
| `boards.greenhouse.io/exemple` | `"greenhouse": ["exemple"]` |
| `jobs.ashbyhq.com/exemple` | `"ashby": ["exemple"]` |
| `exemple.recruitee.com` | `"recruitee": ["exemple"]` |

Une entreprise mal orthographiée n'empêche pas le reste de fonctionner : l'erreur apparaît dans le rapport de collecte.

## Comment une offre est considérée comme clôturée

- Les flux Lever, Greenhouse, Ashby, Recruitee et Remotive ne contiennent que les offres ouvertes.
  Si une offre disparaît du flux, elle est marquée « clôturée ».
- La recherche France Travail étant limitée en nombre de résultats, une offre absente est d'abord
  vérifiée via sa fiche détaillée avant d'être clôturée.
- Si une source est en erreur pendant une collecte, ses offres ne sont **pas** clôturées.
- Les offres clôturées restent visibles (barrées, case « Afficher les clôturées ») pendant 14 jours.

La barre de couleur à gauche indique depuis combien de temps l'offre est suivie :
vert jusqu'à 3 jours, orange jusqu'à 30 jours, gris au-delà. Une offre ouverte depuis longtemps
mérite une vérification avant de postuler.

## Tester en local

```bash
pip install -r requirements.txt
python fetch.py                  # remplit data/jobs.json
python -m http.server 8000       # puis ouvre http://localhost:8000
```

## Règles des sources

- **Remotive** : garder le lien vers l'offre sur Remotive et citer la source (fait automatiquement),
  pas plus de 4 appels par jour (d'où le rythme de 6 h).
- **France Travail** : respecter la licence de réutilisation des offres de francetravail.io.
- **Pages carrières (ATS)** : flux publics prévus pour afficher les offres ; le lien direct est conservé.

## Bon à savoir

- Une page GitHub Pages est **publique**, même si tu es seul à l'utiliser. Ce ne sont que des offres
  déjà publiques, et la balise `noindex` évite qu'elle apparaisse dans Google.
- Dans un dépôt public, GitHub peut désactiver les workflows planifiés après 60 jours sans activité.
  Si les mises à jour s'arrêtent, réactive-le dans l'onglet *Actions*.
