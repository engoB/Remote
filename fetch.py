"""
Collecte d'offres d'emploi en télétravail pour francophones.

Sources (toutes publiques ou officielles) :
  - API Offres d'emploi France Travail (si les secrets FT_CLIENT_ID / FT_CLIENT_SECRET existent)
  - Remotive (API publique : lien vers l'offre + mention de la source obligatoires)
  - Pages carrières d'entreprises via leur ATS : Lever, Greenhouse, Ashby, Recruitee
    (liste dans companies.json)

Chaque offre garde son lien direct d'origine. Une offre qui disparaît du flux d'une
source (interrogée avec succès) est marquée "cloturee".
"""
import html
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    from langdetect import detect, DetectorFactory
    DetectorFactory.seed = 0  # résultats reproductibles
except ImportError:  # la détection de langue est optionnelle
    detect = None

ROOT = Path(__file__).parent
DATA = ROOT / "data" / "jobs.json"
COMPANIES = ROOT / "companies.json"
UA = {"User-Agent": "offres-remote-fr (projet personnel, usage non commercial)"}
NOW = datetime.now(timezone.utc)
GARDER_CLOTUREES_JOURS = 14

ZONES_FR = ["france", "paris", "lyon", "bordeaux", "nantes", "lille", "toulouse", "marseille",
            "belgique", "belgium", "bruxelles", "brussels", "suisse", "switzerland", "genève",
            "geneva", "lausanne", "luxembourg", "québec", "quebec", "montréal", "montreal"]
ZONES_LARGES = ["europe", "emea", "eu", "anywhere", "worldwide", "n'importe", "monde", "global"]
MOTS_REMOTE = re.compile(r"remote|télétravail|teletravail|distanciel|à distance|full[- ]?remote|home[- ]?office", re.I)
MOTS_REMOTE_TOTAL = re.compile(r"100\s?%\s?(télétravail|teletravail|remote)|full[- ]?remote|télétravail (total|complet|intégral)|entièrement à distance", re.I)


# ---------- utilitaires ----------

def texte_brut(s):
    if not s:
        return ""
    s = html.unescape(html.unescape(s))
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def langue(texte):
    if not detect or len(texte) < 80:
        return None
    try:
        return detect(texte[:2000])
    except Exception:
        return None


def zone(lieu):
    l = (lieu or "").lower()
    if any(z in l for z in ZONES_FR):
        return "francophone"
    if any(re.search(rf"\b{re.escape(z)}\b", l) for z in ZONES_LARGES):
        return "europe_monde"
    return "autre"


def get_json(url, **kw):
    r = requests.get(url, headers={**UA, **kw.pop("headers", {})}, timeout=30, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}


def offre(source, id_, titre, entreprise, lieu, description, lien, date=None, remote=None, contrat=None):
    desc = texte_brut(description)
    return {
        "id": f"{source.lower().replace(' ', '_')}-{id_}",
        "source": source,
        "titre": titre.strip(),
        "entreprise": entreprise,
        "lieu": lieu or "",
        "zone": zone(lieu),
        "contrat": contrat,
        "remote": remote,
        "remote_total": bool(MOTS_REMOTE_TOTAL.search(f"{titre} {lieu} {desc}")),
        "langue": langue(desc),
        "description": desc[:500],
        "lien": lien,
        "publiee_le": date,
    }


# ---------- sources ----------

def src_remotive():
    # Remotive demande au plus ~4 requêtes par jour : le workflow tourne toutes les 6 h.
    data = get_json("https://remotive.com/api/remote-jobs")
    for j in data.get("jobs", []):
        yield offre("Remotive", j["id"], j["title"], j["company_name"],
                    j.get("candidate_required_location"), j.get("description"),
                    j["url"], j.get("publication_date"), True, j.get("job_type"))


def src_lever(entreprise):
    for j in get_json(f"https://api.lever.co/v0/postings/{entreprise}?mode=json"):
        cat = j.get("categories", {})
        lieu = cat.get("location", "")
        remote = j.get("workplaceType") == "remote" or bool(MOTS_REMOTE.search(lieu))
        date = datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc).isoformat() if j.get("createdAt") else None
        yield offre("Lever", j["id"], j["text"], entreprise, lieu,
                    j.get("descriptionPlain") or j.get("description"), j["hostedUrl"],
                    date, remote, cat.get("commitment"))


def src_greenhouse(entreprise):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{entreprise}/jobs?content=true")
    for j in data.get("jobs", []):
        lieu = (j.get("location") or {}).get("name", "")
        remote = bool(MOTS_REMOTE.search(f"{lieu} {j['title']}"))
        yield offre("Greenhouse", j["id"], j["title"], entreprise, lieu, j.get("content"),
                    j["absolute_url"], j.get("first_published") or j.get("updated_at"), remote)


def src_ashby(entreprise):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{entreprise}")
    for j in data.get("jobs", []):
        lieu = j.get("location", "")
        remote = bool(j.get("isRemote")) or j.get("workplaceType") == "Remote"
        yield offre("Ashby", j["id"], j["title"], entreprise, lieu,
                    j.get("descriptionPlain") or j.get("descriptionHtml"),
                    j.get("jobUrl") or j.get("applyUrl"), j.get("publishedAt"), remote,
                    j.get("employmentType"))


def src_recruitee(entreprise):
    data = get_json(f"https://{entreprise}.recruitee.com/api/offers/")
    for j in data.get("offers", []):
        lieu = j.get("location", "")
        remote = bool(j.get("remote")) or bool(MOTS_REMOTE.search(lieu))
        yield offre("Recruitee", j["id"], j["title"], j.get("company_name") or entreprise, lieu,
                    j.get("description"), j["careers_url"], j.get("created_at"), remote,
                    j.get("employment_type_code"))


# --- France Travail (API officielle, compte gratuit sur francetravail.io) ---

FT_TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
FT_API = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres"


def ft_token():
    r = requests.post(FT_TOKEN_URL, headers=UA, timeout=30, data={
        "grant_type": "client_credentials",
        "client_id": os.environ["FT_CLIENT_ID"],
        "client_secret": os.environ["FT_CLIENT_SECRET"],
        "scope": "api_offresdemploiv2 o2dsoffre",
    })
    r.raise_for_status()
    return r.json()["access_token"]


def src_france_travail():
    headers = {"Authorization": f"Bearer {ft_token()}", "Accept": "application/json"}
    vus = set()
    for mot in ["teletravail", "full remote"]:
        for debut in range(0, 1050, 150):  # l'API renvoie au plus 150 offres par page
            r = requests.get(f"{FT_API}/search", timeout=30,
                             headers={**UA, **headers},
                             params={"motsCles": mot, "range": f"{debut}-{debut + 149}"})
            if r.status_code == 204:
                break
            r.raise_for_status()
            resultats = r.json().get("resultats", [])
            for j in resultats:
                if j["id"] in vus:
                    continue
                vus.add(j["id"])
                desc = j.get("description", "")
                if not MOTS_REMOTE.search(f"{j.get('intitule', '')} {desc}"):
                    continue
                yield offre("France Travail", j["id"], j.get("intitule", ""),
                            (j.get("entreprise") or {}).get("nom") or "Entreprise non précisée",
                            (j.get("lieuTravail") or {}).get("libelle", "France"), desc,
                            f"https://candidat.francetravail.fr/offres/recherche/detail/{j['id']}",
                            j.get("dateCreation"), True, j.get("typeContratLibelle"))
            if len(resultats) < 150:
                break
            time.sleep(0.5)


def ft_existe_encore(id_ft, token):
    """La recherche France Travail est tronquée : on vérifie une offre absente via son détail."""
    r = requests.get(f"{FT_API}/{id_ft}", timeout=30,
                     headers={**UA, "Authorization": f"Bearer {token}", "Accept": "application/json"})
    return r.status_code == 200


# ---------- fusion avec l'historique ----------

def est_pertinente(o):
    if not o["remote"]:
        return False
    if o["source"] == "France Travail":
        return True
    return o["langue"] == "fr" or o["zone"] in ("francophone", "europe_monde")


def main():
    anciennes = {}
    if DATA.exists():
        anciennes = {o["id"]: o for o in json.loads(DATA.read_text("utf-8")).get("offres", [])}

    entreprises = json.loads(COMPANIES.read_text("utf-8")) if COMPANIES.exists() else {}
    taches = [("Remotive", src_remotive, None)]
    if os.environ.get("FT_CLIENT_ID") and os.environ.get("FT_CLIENT_SECRET"):
        taches.append(("France Travail", src_france_travail, None))
    for ats, fn in [("Lever", src_lever), ("Greenhouse", src_greenhouse),
                    ("Ashby", src_ashby), ("Recruitee", src_recruitee)]:
        for e in entreprises.get(ats.lower(), []):
            taches.append((ats, fn, e))

    trouvees, sources_ok, rapport = {}, set(), []
    for nom, fn, arg in taches:
        cle = f"{nom}:{arg}" if arg else nom
        try:
            n = 0
            for o in (fn(arg) if arg else fn()):
                if est_pertinente(o):
                    o["cle_source"] = cle
                    trouvees[o["id"]] = o
                    n += 1
            sources_ok.add(cle)
            rapport.append(f"OK    {cle}: {n} offres retenues")
        except Exception as ex:
            rapport.append(f"ERREUR {cle}: {ex}")
        time.sleep(1)

    maintenant = NOW.isoformat()
    resultat = {}
    for id_, o in trouvees.items():
        ancienne = anciennes.get(id_, {})
        o["vue_premiere_fois"] = ancienne.get("vue_premiere_fois", maintenant)
        o["vue_derniere_fois"] = maintenant
        o["statut"] = "active"
        resultat[id_] = o

    token_ft = None
    for id_, o in anciennes.items():
        if id_ in resultat:
            continue
        # Source en erreur pendant ce passage : on ne conclut rien, on garde l'offre telle quelle.
        if o.get("cle_source") not in sources_ok:
            resultat[id_] = o
            continue
        if o["statut"] == "active" and o["source"] == "France Travail":
            try:
                token_ft = token_ft or ft_token()
                if ft_existe_encore(id_.split("-", 2)[-1], token_ft):
                    o["vue_derniere_fois"] = maintenant
                    resultat[id_] = o
                    continue
            except Exception:
                resultat[id_] = o
                continue
        if o["statut"] == "active":
            o["statut"] = "cloturee"
            o["cloturee_le"] = maintenant
        age = (NOW - datetime.fromisoformat(o.get("cloturee_le", maintenant))).days
        if age <= GARDER_CLOTUREES_JOURS:
            resultat[id_] = o

    offres = sorted(resultat.values(), key=lambda o: o["vue_premiere_fois"], reverse=True)
    DATA.parent.mkdir(exist_ok=True)
    DATA.write_text(json.dumps({"mise_a_jour": maintenant, "rapport": rapport, "offres": offres},
                               ensure_ascii=False, indent=1), "utf-8")
    print("\n".join(rapport))
    print(f"Total : {sum(o['statut'] == 'active' for o in offres)} actives, "
          f"{sum(o['statut'] == 'cloturee' for o in offres)} clôturées récemment")


if __name__ == "__main__":
    main()
