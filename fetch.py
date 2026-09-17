"""
Collecte d'offres d'emploi en télétravail pour francophones (projet personnel).

Sources publiques ou officielles, toujours avec lien direct vers l'annonce d'origine :
  - France Travail, API Offres d'emploi       (secrets FT_CLIENT_ID / FT_CLIENT_SECRET)
  - France Travail, API Mes évènements emploi (mêmes secrets, si l'API est ajoutée à l'application)
  - Le Forem (Belgique), open data ODWB
  - Remotive et Jobicy (API publiques de job boards remote)
  - Pages carrières d'entreprises : Lever, Greenhouse, Ashby, Recruitee, Workable, SmartRecruiters
    (détection automatique de l'ATS à partir de companies.json)

Fichiers produits dans data/ :
  jobs.json         offres actives et clôturées récemment
  historique.json   un point par jour pour les graphiques
  entreprises.json  résultat de la détection des pages carrières
  evenements.json   événements emploi
"""
import html
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

try:
    from langdetect import detect, DetectorFactory
    DetectorFactory.seed = 0
except ImportError:
    detect = None

ROOT = Path(__file__).parent
DATA = ROOT / "data"
UA = {"User-Agent": "offres-remote-fr (projet personnel non commercial)"}
NOW = datetime.now(timezone.utc)
GARDER_CLOTUREES_JOURS = 14
DELAI_SOURCE_PARTIELLE_JOURS = 3   # recherche tronquée : clôture si l'offre n'est plus vue depuis 3 jours
REDETECTION_JOURS = 7


def charger(nom, defaut):
    p = ROOT / nom
    return json.loads(p.read_text("utf-8")) if p.exists() else defaut


def ecrire(nom, obj):
    (DATA / nom).write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")


CONFIG = charger("config.json", {})

# ---------------------------------------------------------------- analyse du texte

PAYS = [
    ("France", r"\bfrance\b|paris|lyon|bordeaux|nantes|lille|toulouse|marseille|rennes|montpellier|strasbourg|nice\b|grenoble"),
    ("Belgique", r"belgi|bruxelles|brussels|li[eè]ge|namur|charleroi|mons\b|wallonie"),
    ("Suisse", r"suisse|switzerland|schweiz|gen[eè]v|lausanne|zurich|z[üu]rich|b[aâ]le|basel|berne?\b"),
    ("Luxembourg", r"luxemb"),
    ("Canada", r"canada|qu[ée]bec|montr[ée]al|ottawa|toronto|vancouver"),
    ("Monaco", r"monaco"),
    ("Afrique francophone", r"maroc|morocco|tunisi|s[ée]n[ée]gal|c[oô]te d.ivoire|ivory coast|cameroun|cameroon|alg[ée]ri|madagascar|b[ée]nin|togo|rwanda"),
]
REGIONS_LARGES = r"europe|\beu\b|emea|anywhere|worldwide|monde|global|international|n.importe o[uù]"

MOTS_REMOTE = re.compile(r"remote|t[ée]l[ée]travail|distanciel|[àa] distance|home[- ]?office|work from home|t[ée]l[ée]-travail", re.I)
MOTS_REMOTE_TOTAL = re.compile(
    r"100\s?%\s?(t[ée]l[ée]travail|remote)|full[- ]?remote|fully remote|remote[- ]first|"
    r"t[ée]l[ée]travail (total|complet|int[ée]gral)|enti[èe]rement (à|a|en) (distance|t[ée]l[ée]travail)", re.I)
MOTS_FRANCAIS_REQUIS = re.compile(
    r"fran[çc]ais (courant|natif|bilingue|obligatoire|requis|exig[ée])|langue fran[çc]aise|francophone|"
    r"bilingue|fluent in french|french[- ]speaking|native french|french \(native|french is (a )?(must|required)|"
    r"speak french|business french|proficiency in french|french and english|english and french", re.I)


def texte_brut(s):
    if not s:
        return ""
    s = html.unescape(html.unescape(str(s)))
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def langue(texte):
    if not detect or len(texte) < 80:
        return None
    try:
        return detect(texte[:2000])
    except Exception:
        return None


def pays_de(lieu):
    l = (lieu or "").lower()
    trouves = [nom for nom, motif in PAYS if re.search(motif, l)]
    if trouves:
        return trouves
    if re.search(REGIONS_LARGES, l):
        return ["Europe ou monde"]
    return ["Autre"]


def offre(source, id_, titre, entreprise, lieu, description, lien, date=None, remote=None, contrat=None):
    desc = texte_brut(description)
    tout = f"{titre} {lieu} {desc}"
    pays = pays_de(lieu)
    return {
        "id": f"{re.sub(r'[^a-z0-9]+', '_', source.lower())}-{id_}",
        "source": source,
        "titre": texte_brut(titre),
        "entreprise": texte_brut(entreprise) or "Entreprise non précisée",
        "lieu": texte_brut(lieu),
        "pays": pays,
        "zone": "francophone" if pays[0] not in ("Europe ou monde", "Autre") else ("europe_monde" if pays[0] == "Europe ou monde" else "autre"),
        "contrat": contrat,
        "remote": bool(remote) or bool(MOTS_REMOTE.search(tout)),
        "remote_total": bool(MOTS_REMOTE_TOTAL.search(tout)),
        "langue": langue(desc),
        "francais_demande": bool(MOTS_FRANCAIS_REQUIS.search(desc)),
        "description": desc[:320],
        "lien": lien,
        "publiee_le": date,
    }


def est_pertinente(o):
    if not o["remote"] or not o["lien"]:
        return False
    if o["langue"] == "fr" or o["francais_demande"]:
        return True
    return o["zone"] in ("francophone", "europe_monde")


def get(url, **kw):
    headers = {**UA, **kw.pop("headers", {})}
    return requests.get(url, headers=headers, timeout=30, **kw)


def get_json(url, **kw):
    r = get(url, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}


# ---------------------------------------------------------------- job boards remote

def src_remotive(etat):
    for j in get_json("https://remotive.com/api/remote-jobs").get("jobs", []):
        yield offre("Remotive", j["id"], j["title"], j["company_name"], j.get("candidate_required_location"),
                    j.get("description"), j["url"], j.get("publication_date"), True, j.get("job_type"))


def src_jobicy(etat):
    # Jobicy : conserver le lien Jobicy, ne pas interroger plus d'une fois par heure.
    for geo in CONFIG.get("jobicy_geos", ["france", "belgium", "switzerland", "canada", "europe"]):
        data = get_json("https://jobicy.com/api/v2/remote-jobs", params={"count": 100, "geo": geo})
        for j in data.get("jobs", []):
            yield offre("Jobicy", j["id"], j.get("jobTitle", ""), j.get("companyName"), j.get("jobGeo"),
                        j.get("jobDescription") or j.get("jobExcerpt"), j.get("url"), j.get("pubDate"), True,
                        j.get("jobType") if isinstance(j.get("jobType"), str) else None)
        time.sleep(2)
    etat["complet"] = False  # 100 offres max par zone


# ---------------------------------------------------------------- pages carrières (ATS)

def ats_lever(slug):
    data = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not isinstance(data, list):
        raise ValueError("format inattendu")
    for j in data:
        cat = j.get("categories", {})
        date = datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc).isoformat() if j.get("createdAt") else None
        yield offre("Lever", j["id"], j["text"], slug, cat.get("location", ""),
                    j.get("descriptionPlain") or j.get("description"), j["hostedUrl"], date,
                    j.get("workplaceType") == "remote", cat.get("commitment"))


def ats_greenhouse(slug):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    for j in data["jobs"]:
        yield offre("Greenhouse", j["id"], j["title"], slug, (j.get("location") or {}).get("name", ""),
                    j.get("content"), j["absolute_url"], j.get("first_published") or j.get("updated_at"))


def ats_ashby(slug):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    for j in data["jobs"]:
        yield offre("Ashby", j["id"], j["title"], slug, j.get("location", ""),
                    j.get("descriptionPlain") or j.get("descriptionHtml"), j.get("jobUrl") or j.get("applyUrl"),
                    j.get("publishedAt"), bool(j.get("isRemote")) or j.get("workplaceType") == "Remote",
                    j.get("employmentType"))


def ats_recruitee(slug):
    data = get_json(f"https://{slug}.recruitee.com/api/offers/")
    for j in data["offers"]:
        yield offre("Recruitee", j["id"], j["title"], j.get("company_name") or slug, j.get("location", ""),
                    j.get("description"), j["careers_url"], j.get("created_at"), bool(j.get("remote")),
                    j.get("employment_type_code"))


def ats_workable(slug):
    data = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", params={"details": "true"})
    nom = data.get("name") or slug
    for j in data["jobs"]:
        lieu = ", ".join(filter(None, [j.get("city"), j.get("country")]))
        yield offre("Workable", j.get("shortcode") or j.get("id"), j["title"], nom, lieu, j.get("description"),
                    j.get("url") or j.get("shortlink"), j.get("published_on") or j.get("created_at"),
                    bool(j.get("telecommuting")), j.get("employment_type"))


def ats_smartrecruiters(slug):
    data = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params={"limit": 100})
    for j in data["content"]:
        loc = j.get("location") or {}
        lieu = loc.get("fullLocation") or ", ".join(filter(None, [loc.get("city"), loc.get("country")]))
        entreprise = (j.get("company") or {}).get("name") or slug
        ident = (j.get("company") or {}).get("identifier") or slug
        yield offre("SmartRecruiters", j["id"], j["name"], entreprise, lieu, "",
                    f"https://jobs.smartrecruiters.com/{ident}/{j['id']}", j.get("releasedDate"),
                    bool(loc.get("remote")), (j.get("typeOfEmployment") or {}).get("label"))


ATS = {"lever": ats_lever, "greenhouse": ats_greenhouse, "ashby": ats_ashby,
       "recruitee": ats_recruitee, "workable": ats_workable, "smartrecruiters": ats_smartrecruiters}


def detecter_ats(slug):
    """Essaie chaque ATS ; retient le premier qui publie au moins une offre pour cet identifiant."""
    for nom, fn in ATS.items():
        try:
            offres = list(fn(slug))
            if offres:
                return nom, offres
        except Exception:
            pass
        time.sleep(0.3)
    return None, []


def pages_carrieres(trouvees, sources_ok, rapport):
    conf = charger("companies.json", {})
    cache = {e["slug"]: e for e in charger("data/entreprises.json", {}).get("entreprises", [])}
    resultat = []

    a_traiter = [(s, ats) for ats in ATS for s in conf.get(ats, [])]
    a_traiter += [(s, None) for s in conf.get("a_detecter", []) if s not in {x for x, _ in a_traiter}]

    for slug, ats_force in a_traiter:
        entree = cache.get(slug, {"slug": slug})
        ats = ats_force or entree.get("ats")
        offres, erreur = [], None
        try:
            if ats:
                offres = list(ATS[ats](slug))
            else:
                derniere = entree.get("verifiee_le")
                if derniere and NOW - datetime.fromisoformat(derniere) < timedelta(days=REDETECTION_JOURS):
                    resultat.append(entree)
                    continue
                ats, offres = detecter_ats(slug)
        except Exception as ex:
            erreur = str(ex)[:120]
            if not ats_force:
                ats = None  # l'identifiant a peut-être changé d'ATS : nouvelle détection au prochain passage

        cle = f"ats:{slug}"
        retenues = [o for o in offres if est_pertinente(o)]
        for o in retenues:
            o["cle_source"] = cle
            o["page_carriere"] = slug
            trouvees[o["id"]] = o
        if ats and not erreur:
            sources_ok.add(cle)
        nom = next((o["entreprise"] for o in offres if o["entreprise"] != slug), slug)
        resultat.append({"slug": slug, "nom": nom, "ats": ats, "offres_total": len(offres),
                         "offres_retenues": len(retenues), "erreur": erreur,
                         "verifiee_le": NOW.isoformat()})
        time.sleep(0.5)

    connectees = [e for e in resultat if e.get("ats")]
    rapport.append(f"OK    Pages carrières : {len(connectees)}/{len(resultat)} entreprises connectées, "
                   f"{sum(e.get('offres_retenues', 0) for e in resultat)} offres retenues")
    ecrire("entreprises.json", {"mise_a_jour": NOW.isoformat(), "entreprises": sorted(
        resultat, key=lambda e: (e.get("ats") is None, -e.get("offres_retenues", 0), e["slug"]))})


# ---------------------------------------------------------------- France Travail

FT_TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
FT_OFFRES = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"


def ft_actif():
    return bool(os.environ.get("FT_CLIENT_ID") and os.environ.get("FT_CLIENT_SECRET"))


def ft_token(scope):
    r = requests.post(FT_TOKEN_URL, headers=UA, timeout=30, data={
        "grant_type": "client_credentials", "scope": scope,
        "client_id": os.environ["FT_CLIENT_ID"], "client_secret": os.environ["FT_CLIENT_SECRET"]})
    if r.status_code != 200:
        raise RuntimeError(f"jeton refusé pour le scope « {scope} » ({r.status_code}) : {r.text[:150]}")
    return r.json()["access_token"]


def src_france_travail(etat):
    headers = {**UA, "Authorization": f"Bearer {ft_token('api_offresdemploiv2 o2dsoffre')}",
               "Accept": "application/json"}
    vus, total_annonce, recu = set(), 0, 0
    for mot in CONFIG.get("france_travail_mots_cles", ["teletravail"]):
        for debut in range(0, 3000, 150):
            r = requests.get(FT_OFFRES, headers=headers, timeout=30,
                             params={"motsCles": mot, "range": f"{debut}-{debut + 149}", "sort": 1})
            if r.status_code == 204:
                break
            if r.status_code not in (200, 206):
                raise RuntimeError(f"recherche refusée ({r.status_code}) : {r.text[:150]}")
            m = re.search(r"/(\d+)", r.headers.get("Content-Range", ""))
            if m and debut == 0:
                total_annonce += int(m.group(1))
            resultats = r.json().get("resultats", [])
            recu += len(resultats)
            for j in resultats:
                if j["id"] in vus:
                    continue
                vus.add(j["id"])
                yield offre("France Travail", j["id"], j.get("intitule", ""), (j.get("entreprise") or {}).get("nom"),
                            (j.get("lieuTravail") or {}).get("libelle", "France"), j.get("description"),
                            f"https://candidat.francetravail.fr/offres/recherche/detail/{j['id']}",
                            j.get("dateCreation"), False, j.get("typeContratLibelle"))
            if len(resultats) < 150:
                break
            time.sleep(0.3)
    etat["complet"] = recu >= total_annonce


def evenements(rapport):
    conf = CONFIG.get("evenements", {})
    if not conf.get("actif") or not ft_actif():
        ecrire("evenements.json", {"statut": "inactif", "message": "Ajoute l'API « Mes évènements emploi » à ton application France Travail, puis active-la dans config.json.", "evenements": []})
        return
    try:
        token = ft_token(conf["scope"])
        headers = {**UA, "Authorization": f"Bearer {token}", "Accept": "application/json"}
        r = requests.get(conf["url"], headers=headers, timeout=30)
        if r.status_code == 405:
            r = requests.post(conf["url"], headers={**headers, "Content-Type": "application/json"}, json={}, timeout=30)
        if r.status_code not in (200, 206):
            raise RuntimeError(f"HTTP {r.status_code} : {r.text[:150]}")
        brut = r.json()
        liste = brut if isinstance(brut, list) else next((v for v in brut.values() if isinstance(v, list)), [])
        evts = []
        for e in liste:
            desc = texte_brut(e.get("description"))
            en_ligne = bool(e.get("urlSalonEnLigne")) or bool(re.search(r"en ligne|distanc|visio|webinaire|web ?conf", f"{e.get('titre','')} {desc} {e.get('modalite','')}", re.I))
            evts.append({"titre": texte_brut(e.get("titre")), "type": e.get("typeEvenement") or "Autre",
                         "debut": e.get("dateDebut"), "fin": e.get("dateFin"),
                         "lieu": texte_brut(e.get("localisation")), "organisateur": e.get("organismeOrganisateur"),
                         "offres": e.get("nombreOffres"), "en_ligne": en_ligne, "description": desc[:250],
                         "lien": e.get("urlSalonEnLigne") or e.get("url") or "https://mesevenementsemploi.francetravail.fr/"})
        ecrire("evenements.json", {"statut": "ok", "mise_a_jour": NOW.isoformat(), "evenements": evts})
        rapport.append(f"OK    Événements France Travail : {len(evts)} ({sum(e['en_ligne'] for e in evts)} en ligne)")
    except Exception as ex:
        ecrire("evenements.json", {"statut": "erreur", "message": str(ex)[:300], "evenements": []})
        rapport.append(f"ERREUR Événements France Travail : {str(ex)[:200]}")


# ---------------------------------------------------------------- Le Forem (Belgique)

def _champ(rec, *indices):
    for cle, val in rec.items():
        if val and any(i in cle.lower() for i in indices):
            return val
    return None


def src_forem(etat):
    base = "https://www.odwb.be/api/explore/v2.1/catalog/datasets/offres-d-emploi-forem/records"
    recherche = CONFIG.get("forem", {}).get("recherche", "télétravail")
    recu, total = 0, None
    for offset in range(0, 2000, 100):
        data = get_json(base, params={"where": f'"{recherche}"', "limit": 100, "offset": offset})
        total = data.get("total_count", 0)
        recs = data.get("results", [])
        if offset == 0 and recs:
            etat["info"] = "champs : " + ", ".join(list(recs[0].keys())[:25])
        for rec in recs:
            lien = _champ(rec, "url", "lien", "link")
            ident = _champ(rec, "numero", "id_offre", "reference", "identifiant") or lien
            titre = _champ(rec, "titre", "intitule", "libelle_metier", "metier", "fonction")
            if not (lien and titre):
                continue
            lieu = _champ(rec, "lieu", "localite", "commune", "region") or "Belgique"
            if isinstance(lieu, list):
                lieu = ", ".join(map(str, lieu))
            yield offre("Le Forem", ident, str(titre), _champ(rec, "employeur", "entreprise", "nom_entreprise"),
                        f"{lieu}, Belgique", _champ(rec, "description", "resume", "descriptif") or "",
                        lien, _champ(rec, "date_publication", "datepublication", "date_creation"), False,
                        _champ(rec, "contrat", "regime"))
        recu += len(recs)
        if len(recs) < 100:
            break
        time.sleep(0.5)
    etat["complet"] = total is not None and recu >= total


# ---------------------------------------------------------------- fusion et historique

def main():
    DATA.mkdir(exist_ok=True)
    anciennes = {o["id"]: o for o in charger("data/jobs.json", {}).get("offres", [])}
    trouvees, sources_ok, partielles, rapport = {}, set(), set(), []

    sources = [("Remotive", src_remotive), ("Jobicy", src_jobicy)]
    if CONFIG.get("forem", {}).get("actif", True):
        sources.append(("Le Forem", src_forem))
    if ft_actif():
        sources.append(("France Travail", src_france_travail))
    else:
        rapport.append("INFO  France Travail : ajoute les secrets FT_CLIENT_ID et FT_CLIENT_SECRET pour l'activer")

    for nom, fn in sources:
        etat = {"complet": True}
        try:
            n = 0
            for o in fn(etat):
                if est_pertinente(o):
                    o["cle_source"] = nom
                    trouvees[o["id"]] = o
                    n += 1
            sources_ok.add(nom)
            if not etat["complet"]:
                partielles.add(nom)
            info = f" ({etat['info']})" if etat.get("info") and n == 0 else ""
            rapport.append(f"OK    {nom} : {n} offres retenues{info}")
        except Exception as ex:
            rapport.append(f"ERREUR {nom} : {str(ex)[:250]}")
        time.sleep(1)

    pages_carrieres(trouvees, sources_ok, rapport)
    evenements(rapport)

    maintenant = NOW.isoformat()
    resultat, nouvelles, cloturees = {}, 0, 0
    for id_, o in trouvees.items():
        ancienne = anciennes.get(id_)
        if not ancienne:
            nouvelles += 1
        o["vue_premiere_fois"] = (ancienne or {}).get("vue_premiere_fois", maintenant)
        o["vue_derniere_fois"] = maintenant
        o["statut"] = "active"
        resultat[id_] = o

    for id_, o in anciennes.items():
        if id_ in resultat:
            continue
        cle = o.get("cle_source")
        vue_il_y_a = NOW - datetime.fromisoformat(o.get("vue_derniere_fois", maintenant))
        garder_active = (cle not in sources_ok) or (cle in partielles and vue_il_y_a < timedelta(days=DELAI_SOURCE_PARTIELLE_JOURS))
        if o.get("statut") == "active" and garder_active:
            resultat[id_] = o
            continue
        if o.get("statut") == "active":
            o["statut"] = "cloturee"
            o["cloturee_le"] = maintenant
            cloturees += 1
        if NOW - datetime.fromisoformat(o.get("cloturee_le", maintenant)) <= timedelta(days=GARDER_CLOTUREES_JOURS):
            resultat[id_] = o

    offres = sorted(resultat.values(), key=lambda o: o["vue_premiere_fois"], reverse=True)
    actives = [o for o in offres if o["statut"] == "active"]
    ecrire("jobs.json", {"mise_a_jour": maintenant, "rapport": rapport, "offres": offres})

    historique = charger("data/historique.json", [])
    jour = NOW.date().isoformat()
    point = next((p for p in historique if p["date"] == jour), None)
    if not point:
        point = {"date": jour, "nouvelles": 0, "cloturees": 0}
        historique.append(point)
    par_source = {}
    for o in actives:
        par_source[o["source"]] = par_source.get(o["source"], 0) + 1
    point.update({"actives": len(actives), "par_source": par_source,
                  "nouvelles": point["nouvelles"] + (nouvelles if anciennes else 0),
                  "cloturees": point["cloturees"] + cloturees})
    ecrire("historique.json", historique[-120:])

    print("\n".join(rapport))
    print(f"Total : {len(actives)} actives, {nouvelles} nouvelles, {cloturees} clôturées pendant ce passage")


if __name__ == "__main__":
    main()
