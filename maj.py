"""Mises à jour de Djåse (ajoutées en 0.2.0, décision de Manu du 06/11/2026).

Au lancement (une minute après « prêt »), puis toutes les 24 h tant que Djåse tourne, Djåse lit sur GitHub le numéro de
la dernière version publiée. La date de la dernière vérification est gardée dans maj.json : relancer Djåse ne
multiplie pas les requêtes. Une case des Réglages (« Rechercher les mises à jour ») coupe toute vérification
automatique ; le bouton « Vérifier maintenant » de la page À propos marche dans tous les cas (geste explicite).

La seule requête automatique est cette lecture (https://api.github.com/repos/ManuelWarland/djase/releases/latest) :
aucune dictée, aucun identifiant, un User-Agent « Djase/<version> » sans adresse. Le téléchargement de l'installateur
n'a lieu que sur un clic de « Mettre à jour ».

Sécurité de l'installation :
- l'installateur est l'élément de la release nommé Djase-<version>-installation.exe, dont l'adresse doit être
  exactement celle du dépôt (https://github.com/ManuelWarland/djase/releases/download/<tag>/<nom>) ;
- son empreinte SHA-256 vient du champ `digest` de GitHub ; sans elle, pas d'installation automatique (la page de la
  release s'ouvre, c'est tout) ;
- il est téléchargé sous un nom .partiel (repris là où il s'était arrêté), contrôlé (taille, SHA-256), renommé
  seulement s'il est complet et juste, puis contrôlé une seconde fois juste avant d'être lancé ;
- il n'est lancé que par la version installée (Djase.exe), jamais en développement.
"""
import hashlib
import http.client
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request

from chemins import DONNEES, FIGE
from version import VERSION

DEPOT = "ManuelWarland/djase"
URL_DEPOT = f"https://github.com/{DEPOT}"
API_DERNIERE = f"https://api.github.com/repos/{DEPOT}/releases/latest"
PREFIXE_TELECHARGEMENT = f"{URL_DEPOT}/releases/download/"
AGENT = f"Djase/{VERSION}"
DOSSIER = DONNEES / "mises_a_jour"
FICHIER = DONNEES / "maj.json"
INTERVALLE = 24 * 3600          # une vérification automatique par jour au plus, même après des relances
ATTENTE_ERREUR = 3600           # après un échec (réseau coupé), nouvel essai automatique au plus une fois par heure
TAILLE_MAX = 2 * 1024 ** 3      # un « installateur » plus gros que cela n'est pas le nôtre (il fait 80 Mo)
ARGUMENTS_INSTALLATEUR = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"  # aucune fenêtre : le focus de la personne ne bouge pas


class ErreurMaj(Exception):
    """Message court, en français, montré tel quel dans la fenêtre."""


# ---- Versions ------------------------------------------------------------------------------------------------------

def version_en_tuple(texte):
    """« v0.10.0 » -> (0, 10, 0, 0) ; None si le texte n'est pas un numéro de version lisible (« v0.3-beta », « latest »).
    Comparés en nombres : 0.10.0 est plus récent que 0.9.0. Complété à 4 nombres : 0.2 vaut 0.2.0."""
    if not isinstance(texte, str):
        return None
    m = re.fullmatch(r"[vV]?(\d{1,6}(?:\.\d{1,6}){0,3})", texte.strip(), re.ASCII)  # ASCII : pas de chiffres arabes ou autres
    if not m:
        return None
    nombres = [int(x) for x in m.group(1).split(".")]
    return tuple(nombres + [0] * (4 - len(nombres)))


def plus_recente(distante, locale=VERSION):
    """True si `distante` (tuple ou texte) est strictement plus récente que `locale`. Illisible : False."""
    a = version_en_tuple(distante) if isinstance(distante, str) else distante
    b = version_en_tuple(locale) if isinstance(locale, str) else locale
    return isinstance(a, tuple) and isinstance(b, tuple) and a > b


# ---- Lecture de GitHub ---------------------------------------------------------------------------------------------

def _message_reseau(e):
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (403, 429):
            return "GitHub limite les demandes pour l'instant, réessayez plus tard"
        if e.code == 404:
            return "aucune version publiée sur GitHub"
        return f"GitHub a répondu {e.code}"
    if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):
        return "GitHub ne répond pas"
    return "pas de connexion à GitHub"


def choisir_installateur(assets, tag):
    """(infos, raison) : l'élément `Djase-<version>-installation.exe` de la release, ou None avec la raison pour laquelle
    l'installation automatique est refusée. `tag` a déjà été validé par version_en_tuple."""
    nom = f"Djase-{tag.strip().lstrip('vV')}-installation.exe"
    trouve = next((a for a in assets if isinstance(a, dict) and a.get("name") == nom), None) \
        if isinstance(assets, list) else None
    if trouve is None:
        return None, f"{nom} n'est pas dans la release"
    url = trouve.get("browser_download_url")
    if url != f"{PREFIXE_TELECHARGEMENT}{tag}/{nom}":
        return None, "adresse de téléchargement hors du dépôt de Djåse"
    digest = trouve.get("digest")
    m = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", digest) if isinstance(digest, str) else None
    if not m:
        return None, "GitHub ne donne pas l'empreinte SHA-256 de l'installateur"
    taille = trouve.get("size")
    if not isinstance(taille, int) or isinstance(taille, bool) or not 0 < taille <= TAILLE_MAX:
        return None, "taille de l'installateur invraisemblable"
    return {"nom": nom, "url": url, "taille": taille, "sha256": m.group(1).lower()}, ""


def lire_derniere_release():
    """La dernière release publiée : {tag, version, tuple, page, notes, installateur, raison}. `tuple` vaut None si le tag
    est illisible (alors : pas de mise à jour). Lève ErreurMaj (message court) si GitHub est injoignable."""
    requete = urllib.request.Request(API_DERNIERE, headers={"User-Agent": AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(requete, timeout=15) as r:
            brut = r.read(2_000_000)
    except (OSError, http.client.HTTPException) as e:  # HTTPError, URLError, délai dépassé, coupure
        raise ErreurMaj(_message_reseau(e)) from e
    try:
        donnees = json.loads(brut.decode("utf-8"))
    except ValueError:
        donnees = None
    if not isinstance(donnees, dict):
        raise ErreurMaj("réponse de GitHub illisible")
    tag = donnees.get("tag_name")
    t = None if donnees.get("draft") or donnees.get("prerelease") else version_en_tuple(tag)
    if t is None:
        return {"tag": str(tag), "version": "", "tuple": None, "page": f"{URL_DEPOT}/releases/latest", "notes": "",
                "installateur": None, "raison": "numéro de version illisible"}
    tag = tag.strip()
    infos, raison = choisir_installateur(donnees.get("assets"), tag)
    notes = donnees.get("body")
    return {"tag": tag, "version": tag.lstrip("vV"), "tuple": t, "page": f"{URL_DEPOT}/releases/tag/{tag}",
            "notes": notes[:4000] if isinstance(notes, str) else "", "installateur": infos, "raison": raison}


# ---- Liens ouverts dans le navigateur ------------------------------------------------------------------------------

def lien_autorise(url):
    """Seules les adresses du dépôt de Djåse s'ouvrent depuis la fenêtre (pas djase-evil, pas un autre site). Après
    l'adresse du dépôt : rien, ou un chemin, une requête ou une ancre, en caractères d'une liste blanche, sans « .. »
    ni « %2e » (point encodé, quelle que soit la casse)."""
    if not isinstance(url, str) or not url.startswith(URL_DEPOT):
        return False
    suite = url[len(URL_DEPOT):]
    if re.fullmatch(r"(?:[/?#][A-Za-z0-9._~/%=&?#-]*)?", suite, re.ASCII) is None:
        return False
    return ".." not in suite and "%2e" not in suite.lower()


# ---- Téléchargement ------------------------------------------------------------------------------------------------

def _mode_fil(drapeau):
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentThread.restype = wintypes.HANDLE
        k32.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
        k32.SetThreadPriority.restype = wintypes.BOOL
        k32.SetThreadPriority(k32.GetCurrentThread(), drapeau)
    except Exception:
        pass  # sans effet si Windows refuse


def _priorite_basse():
    """Priorité « arrière-plan » (disque et processeur) pour le fil appelant, comme telechargement.py : sur un disque
    mécanique, ce qu'écrit Djåse ne doit pas ralentir ce que Manu fait en même temps."""
    _mode_fil(0x00010000)  # THREAD_MODE_BACKGROUND_BEGIN


def _priorite_normale():
    """Fin du mode arrière-plan du fil, avant de lancer l'installateur : il ne doit pas hériter d'une priorité réduite."""
    _mode_fil(0x00020000)  # THREAD_MODE_BACKGROUND_END


def _message_disque(e, dossier):
    """Une erreur d'écriture ou de lecture sur le disque n'est pas une coupure réseau : message clair, pas de nouvel essai."""
    code, win = getattr(e, "errno", None), getattr(e, "winerror", None)
    if code == 28 or win in (39, 112):
        return "disque plein : libérez de la place puis réessayez (rien n'est perdu)"
    if isinstance(e, PermissionError) or code == 13 or win in (5, 32, 33):
        return (f"accès refusé ou fichier verrouillé dans {dossier} (antivirus, droits du dossier) : "
                f"rien n'est perdu, réessayez une fois le problème réglé")
    return f"impossible d'écrire dans {dossier} ({getattr(e, 'strerror', None) or e})"


def _controle(chemin, taille, sha256):
    if chemin.stat().st_size != taille:
        return False
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest() == sha256


def _recevoir(url, partiel, deja, taille, progres):
    """Un passage de téléchargement, repris à l'octet `deja`. Rend sans erreur quand le flux se termine, COMPLET OU NON
    (coupure silencieuse du serveur : read() rend b"" avant la fin) ; l'appelant compare la taille et recommence."""
    entetes = {"User-Agent": AGENT}
    if deja:
        entetes["Range"] = f"bytes={deja}-"
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=entetes), timeout=60)
    except urllib.error.HTTPError as e:
        if e.code != 416:
            raise
        deja = 0  # reprise refusée : on repart de zéro
        r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": AGENT}), timeout=60)
    with r:
        if deja and (r.status != 206 or not r.headers.get("Content-Range", "").startswith(f"bytes {deja}-")):
            deja = 0  # le serveur ne reprend pas là où il faut : on repart de zéro
        try:
            f = open(partiel, "ab" if deja else "wb")
        except OSError as e:
            raise ErreurMaj(_message_disque(e, DOSSIER)) from e  # disque, pas réseau : pas de nouvel essai
        try:
            recu = deja
            while recu < taille:
                bloc = r.read(min(1 << 18, taille - recu))  # jamais plus que la taille annoncée
                if not bloc:
                    break
                try:
                    f.write(bloc)
                except OSError as e:
                    raise ErreurMaj(_message_disque(e, DOSSIER)) from e
                recu += len(bloc)
                if progres:
                    progres(recu, taille)
        finally:
            try:
                f.close()
            except OSError as e:
                raise ErreurMaj(_message_disque(e, DOSSIER)) from e


def _remplir(partiel, url, taille, progres, attente):
    """Reçoit le .partiel jusqu'à `taille` octets : reprise (Range) après chaque coupure, jusqu'à 8 échecs d'affilée
    (un passage qui gagne des octets ne compte pas comme un échec). Ne supprime jamais un fichier simplement incomplet."""
    echecs, vu = 0, -1
    while True:
        deja = partiel.stat().st_size if partiel.exists() else 0
        if deja > taille:
            partiel.unlink()  # plus gros que prévu : ne peut pas être le début du bon fichier
            deja = 0
        if progres:
            progres(deja, taille)
        if deja == taille:
            return
        if deja > vu:
            echecs, vu = 0, deja  # du terrain gagné
        if echecs >= 8:
            raise ErreurMaj(f"connexion coupée {echecs} fois de suite ; rien n'est perdu, réessayez plus tard")
        echecs += 1
        try:
            _recevoir(url, partiel, deja, taille, progres)
        except urllib.error.HTTPError as e:
            if e.code not in (408, 429, 500, 502, 503, 504):
                raise ErreurMaj(f"GitHub a répondu {e.code} pour l'installateur") from e
            time.sleep(min(30, attente * echecs))
        except (OSError, http.client.HTTPException):
            # connexion coupée, délai dépassé, réception tronquée. Les erreurs de DISQUE ne passent pas ici : _recevoir
            # les convertit en ErreurMaj (message clair, aucun nouvel essai)
            time.sleep(min(30, attente * echecs))


def _telecharger(infos, progres, attente, journal):
    nom, url, taille, sha = infos["nom"], infos["url"], infos["taille"], infos["sha256"]
    DOSSIER.mkdir(parents=True, exist_ok=True)
    cible = DOSSIER / nom
    partiel = DOSSIER / (nom + ".partiel")
    if cible.exists():
        reel = cible.stat().st_size
        if reel == taille:
            if _controle(cible, taille, sha):
                return cible  # déjà téléchargé et vérifié (clic précédent, ou lancement qui n'a pas abouti)
            cible.unlink()  # complet mais faux : sans valeur
        elif reel < taille:
            # sous son nom final mais incomplet : devient le .partiel (reprise), sauf si un .partiel plus avancé existe
            if partiel.exists() and partiel.stat().st_size >= reel:
                cible.unlink()
            else:
                os.replace(cible, partiel)
        else:
            cible.unlink()  # plus gros que prévu : ne peut pas être le bon
    for passage in (1, 2):
        _remplir(partiel, url, taille, progres, attente)
        if _controle(partiel, taille, sha):
            partiel.replace(cible)
            return cible
        partiel.unlink(missing_ok=True)  # complet mais faux : le seul cas où l'on supprime
        if passage == 1:
            if journal:
                journal("Mises à jour : fichier complet mais empreinte fausse, il est supprimé et retéléchargé une fois.")
            if progres:
                progres(0, taille)
    raise ErreurMaj("l'empreinte SHA-256 de l'installateur ne correspond pas, même après un second téléchargement : "
                    "fichier supprimé, rien n'a été lancé")


def telecharger(infos, progres=None, attente=3, journal=None):
    """Télécharge l'installateur dans DOSSIER et rend son chemin, une fois complet et d'empreinte juste. Coupures
    du serveur : reprise (voir _remplir). Un .partiel complet mais faux est supprimé puis retéléchargé UNE fois depuis
    zéro, avant de déclarer l'échec. Disque plein, accès refusé : message clair tout de suite, sans nouvel essai.
    `attente` : base de la pause entre deux essais."""
    try:
        return _telecharger(infos, progres, attente, journal)
    except OSError as e:  # les erreurs réseau sont absorbées plus bas : ce qui arrive ici touche le disque
        raise ErreurMaj(_message_disque(e, DOSSIER)) from e


# ---- Lancement de l'installateur ----------------------------------------------------------------------------------

def lancer_installateur(chemin, infos, arguments=ARGUMENTS_INSTALLATEUR):
    """Contrôle l'empreinte une dernière fois, puis lance l'installateur DÉTACHÉ de Djåse.

    L'installateur fait `taskkill /IM Djase.exe /T /F` (djase.iss, PrepareToInstall) : /T tue Djase.exe ET ses
    descendants. Il passe donc par `cmd /c start`, qui rend la main aussitôt : l'installateur a alors pour parent un
    cmd.exe déjà terminé, hors de l'arbre de Djase.exe. Le Job « tuer à la fermeture » de Djåse (dictee.lier_a_l_appli)
    ne contient que le serveur Qwen, jamais Djase.exe ni ses autres descendants ; CREATE_BREAKAWAY_FROM_JOB sert
    seulement si Djåse avait été lancé dans un Job étranger (repli sans lui s'il est refusé).
    Le chemin passe par une variable d'environnement : « % » ou « & » dans un nom de dossier ne casse pas la commande."""
    if not FIGE:
        raise ErreurMaj("version de développement : l'installateur n'est lancé que par la version installée")
    try:
        bon = _controle(chemin, infos["taille"], infos["sha256"])
    except OSError as e:
        raise ErreurMaj(_message_disque(e, DOSSIER)) from e
    if not bon:
        raise ErreurMaj("l'empreinte de l'installateur n'est plus la bonne : rien n'a été lancé")
    _priorite_normale()
    shell = os.environ.get("COMSPEC") or "cmd.exe"
    commande = f'"{shell}" /d /c start "" "%DJASE_INSTALLATEUR%" {arguments}'
    env = {**os.environ, "DJASE_INSTALLATEUR": str(chemin)}
    # Aucune fenêtre : DETACHED_PROCESS (cmd n'a pas de console) et SW_HIDE (au cas où) ; l'installateur, lui, est lancé
    # avec /VERYSILENT. Une fenêtre qui s'ouvre prendrait le focus de la personne.
    cache = subprocess.STARTUPINFO()
    cache.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    cache.wShowWindow = subprocess.SW_HIDE
    detache = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    options = dict(env=env, close_fds=True, startupinfo=cache,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        proc = subprocess.Popen(commande, creationflags=detache | 0x01000000, **options)
    except OSError:  # CREATE_BREAKAWAY_FROM_JOB refusé (le Job de Djåse ne l'autorise pas) : sans lui
        proc = subprocess.Popen(commande, creationflags=detache, **options)
    try:
        if proc.wait(timeout=15) != 0:
            raise ErreurMaj("Windows n'a pas pu lancer l'installateur")
    except subprocess.TimeoutExpired:
        pass  # cmd ne se termine pas : l'installateur est sans doute parti, on ne tue rien


def nettoyer_anciens():
    """Supprime de DOSSIER les installateurs (et .partiel) d'une version égale ou plus ancienne que celle qui tourne :
    ils ont servi ou ne serviront plus. Un .partiel d'une version PLUS RÉCENTE est gardé (reprise possible)."""
    supprimes = []
    if not DOSSIER.is_dir():
        return supprimes
    for f in DOSSIER.iterdir():
        m = re.fullmatch(r"Djase-(\d+(?:\.\d+){0,3})-installation\.exe(?:\.partiel)?", f.name, re.ASCII)
        if m and f.is_file() and not plus_recente(version_en_tuple(m.group(1))):
            try:
                f.unlink()
                supprimes.append(f.name)
            except OSError:
                pass
    return supprimes


# ---- Le service, côté Djåse ----------------------------------------------------------------------------------------

class MisesAJour:
    """Vérification automatique, état montré à la fenêtre, téléchargement et lancement de l'installateur.
    `d` : le module dictee (REGLAGES, ETAT) ; `journal(texte)` ; `message(texte)` : la pastille ; `quitter()` : le même
    chemin que « Quitter Djåse » du menu de l'icône."""

    def __init__(self, d, journal, message, quitter):
        self.d, self.journal, self.message, self.quitter = d, journal, message, quitter
        self.phase = "repos"        # repos, verification, telechargement, installation
        self.erreur = ""            # dernier échec (vérification ou installation), effacé par la réussite suivante
        self._erreur_install = ""   # installation lancée au démarrage précédent qui n'a pas abouti (voir _apres_installation)
        self.fait = self.total = 0  # octets reçus / attendus pendant le téléchargement
        self.release = None         # dernière release lue pendant CETTE session (infos de l'installateur)
        self._verrou = threading.Lock()
        sauve = self._lire()
        nombre = lambda x: x if isinstance(x, (int, float)) and not isinstance(x, bool) else None
        texte = lambda x: x if isinstance(x, str) else None
        self.verifie = nombre(sauve.get("verifie"))
        self._dernier_essai = nombre(sauve.get("essai")) or 0.0  # gardé : après un échec, une relance ne redemande pas tout de suite
        self._dispo = texte(sauve.get("disponible"))
        self.annoncee = texte(sauve.get("annoncee"))
        # installateur lancé par Djåse avant sa fermeture : a-t-il abouti ? (vu au lancement suivant, _apres_installation)
        self._install = ({"vers": sauve["installation_vers"], "le": nombre(sauve.get("le"))}
                         if texte(sauve.get("installation_vers")) else None)

    # -- fichier d'état --

    @staticmethod
    def _lire():
        try:
            donnees = json.loads(FICHIER.read_text(encoding="utf-8"))
            return donnees if isinstance(donnees, dict) else {}
        except (OSError, ValueError):
            return {}

    def _sauver(self):
        donnees = {"verifie": self.verifie, "essai": self._dernier_essai, "disponible": self._dispo,
                   "annoncee": self.annoncee}
        if self._install:
            donnees.update(installation_vers=self._install["vers"], le=self._install["le"])
        texte = json.dumps(donnees, ensure_ascii=False, indent=2)
        provisoire = FICHIER.with_name(FICHIER.name + ".tmp")
        try:
            provisoire.write_text(texte + "\n", encoding="utf-8")
            os.replace(provisoire, FICHIER)
        except OSError as e:
            self.journal(f"Mises à jour : état pas enregistré ({e}).")

    # -- état montré à la fenêtre --

    @property
    def disponible(self):
        """La version plus récente connue, ou None (une version déjà installée depuis ne compte plus)."""
        return self._dispo if plus_recente(self._dispo) else None

    def etat(self):
        dispo = self.disponible
        rel = self.release if dispo and self.release and self.release["version"] == dispo else None
        installable = bool(FIGE and (rel is None or rel["installateur"]))
        note = ""
        if dispo and not installable:
            note = ("Cette version de développement ne s'installe pas toute seule." if not FIGE else
                    f"Installation automatique impossible ({rel['raison']}) : téléchargez l'installateur sur la page de la version.")
        return {"version": VERSION, "automatique": bool(self.d.REGLAGES.get("verifier_maj", True)),
                "phase": self.phase, "verifie": self.verifie, "disponible": dispo,
                "page": rel["page"] if rel else f"{URL_DEPOT}/releases/latest",
                "erreur": self.erreur or self._erreur_install, "installable": installable, "note": note,
                "fait": self.fait, "total": self.total}

    # -- vérification --

    def demarrer(self):
        threading.Thread(target=self._boucle, daemon=True, name="mises-a-jour").start()

    def _echeance(self):
        if not self.d.REGLAGES.get("verifier_maj", True):
            return False
        maintenant = time.time()
        if self.verifie is not None and 0 <= maintenant - self.verifie < INTERVALLE:
            return False
        return maintenant - self._dernier_essai >= ATTENTE_ERREUR

    def _boucle(self):
        # pas pendant le chargement des modèles : on attend « prêt » (10 min au plus : un moteur en panne doit
        # pouvoir recevoir la mise à jour qui le répare), puis une minute
        limite = time.time() + 600
        while not self.d.ETAT.get("tout_pret") and time.time() < limite:
            time.sleep(5)
        if FIGE:
            try:
                self._apres_installation()
            except Exception as e:
                self.journal(f"Mises à jour : suite de l'installation illisible ({e}).")
        time.sleep(60)
        if FIGE:
            try:
                for nom in nettoyer_anciens():
                    self.journal(f"Mises à jour : ancien installateur supprimé ({nom}).")
            except Exception as e:
                self.journal(f"Mises à jour : nettoyage impossible ({e}).")
        while True:
            try:
                if self._echeance():
                    self.verifier(auto=True, bloquant=True)
            except Exception as e:
                self.journal(f"Mises à jour : {e}")
            time.sleep(600)  # on regarde l'heure toutes les 10 min (le PC peut avoir dormi, la case avoir changé)

    def _apres_installation(self):
        """Djåse se ferme sans savoir si l'installateur a abouti : avant de le lancer, il note la version visée dans
        maj.json. Au lancement suivant (version installée seulement), on compare. La note est effacée dans les deux cas,
        le message ne revient donc pas à chaque démarrage."""
        if not self._install:
            return
        vers = self._install["vers"]
        self._install = None
        self._sauver()
        cible = version_en_tuple(vers)
        if cible is None:
            return
        if plus_recente(cible):  # la version visée est encore plus récente que celle qui tourne : installation ratée
            self._erreur_install = f"la mise à jour vers {vers} n'a pas abouti"  # reste affichée jusqu'au prochain « Mettre à jour »
            self.journal(f"Mises à jour : la mise à jour vers {vers} n'a pas abouti (Djåse {VERSION} tourne encore).")
            self.message(f"La mise à jour vers {vers} n'a pas abouti : téléchargez l'installateur depuis la page À propos")
        else:
            self.journal(f"Mises à jour : mise à jour vers {vers} réussie, Djåse {VERSION} tourne.")
            self.message(f"Djåse est à jour (version {VERSION})")

    def verifier(self, auto=False, bloquant=False):
        """Lance une vérification. False si une opération est déjà en cours."""
        with self._verrou:
            if self.phase != "repos":
                return False
            self.phase = "verification"
        if bloquant:
            self._verifier(auto)
        else:
            threading.Thread(target=self._verifier, args=(auto,), daemon=True, name="verification-maj").start()
        return True

    def _verifier(self, auto):
        try:
            self._consulter(auto)
        except ErreurMaj as e:
            self.erreur = str(e)
            self.journal(f"Mises à jour : {e}.")
        except Exception as e:
            self.erreur = "erreur inattendue (voir le Journal)"
            self.journal(f"Mises à jour : erreur inattendue ({type(e).__name__} : {e}).")
        finally:
            self.phase = "repos"

    def _consulter(self, auto):
        """Lit GitHub et met l'état à jour ; rend la release. Lève ErreurMaj si GitHub est injoignable."""
        self._dernier_essai = time.time()
        self._sauver()  # l'heure de l'ESSAI, réussi ou non : après un échec, pas plus d'un essai par heure même après relances
        rel = lire_derniere_release()
        self.release = rel
        self.verifie = time.time()
        self.erreur = ""
        origine = "automatique" if auto else "demandée"
        if rel["tuple"] is None:
            self._dispo = None
            self.journal(f"Mises à jour : vérification {origine}, numéro de version illisible dans la release ({rel['tag']!r}) : ignorée.")
        elif plus_recente(rel["tuple"]):
            self._dispo = rel["version"]
            self.journal(f"Mises à jour : vérification {origine}, la version {rel['version']} est disponible (vous avez {VERSION}).")
            if auto and self.annoncee != rel["version"]:
                # formule neutre : la fenêtre web (page À propos) peut manquer si ni Chrome ni Edge n'est installé
                self.message(f"Djåse {rel['version']} est disponible sur github.com/ManuelWarland/djase")
            self.annoncee = rel["version"]
        else:
            self._dispo = None
            self.journal(f"Mises à jour : vérification {origine}, Djåse {VERSION} est à jour.")
        self._sauver()
        return rel

    # -- installation --

    def installer(self):
        """« Mettre à jour » : relit la release (empreinte fraîche), télécharge, contrôle, lance l'installateur, puis
        Djåse se ferme. False si une opération est déjà en cours."""
        with self._verrou:
            if self.phase != "repos":
                return False
            self.phase = "verification"
            self.erreur = self._erreur_install = ""
        threading.Thread(target=self._installer, daemon=True, name="installation-maj").start()
        return True

    def _installer(self):
        lance = False
        try:
            _priorite_basse()
            rel = self._consulter(False)
            if not plus_recente(rel["tuple"]):
                raise ErreurMaj("aucune version plus récente n'est publiée")
            if not FIGE:
                raise ErreurMaj("version de développement : pas d'installation automatique")
            infos = rel["installateur"]
            if infos is None:
                raise ErreurMaj(f"installation automatique impossible ({rel['raison']}) ; téléchargez l'installateur sur la page de la version")
            self.fait, self.total = 0, infos["taille"]
            self.phase = "telechargement"
            self.journal(f"Mises à jour : téléchargement de {infos['nom']} ({infos['taille'] / 1e6:.0f} Mo).")

            def progres(fait, total):
                self.fait, self.total = fait, total
            chemin = telecharger(infos, progres, journal=self.journal)
            self.journal("Mises à jour : installateur téléchargé, empreinte SHA-256 juste.")
            self.phase = "installation"
            # noté AVANT le lancement : Djåse se ferme sans savoir si l'installateur aboutit (voir _apres_installation)
            self._install = {"vers": rel["version"], "le": time.time()}
            self._sauver()
            try:
                lancer_installateur(chemin, infos)
            except Exception:
                self._install = None
                self._sauver()
                raise
            lance = True
            self.journal("Mises à jour : installateur lancé (invisible), Djåse se ferme dans 2 s puis redémarre tout seul.")
            self.message(f"Mise à jour vers {rel['version']} en cours : Djåse revient dans une minute")
            threading.Timer(2.0, self.quitter).start()
        except ErreurMaj as e:
            self.erreur = str(e)
            self.journal(f"Mises à jour : échec, {e}.")
        except Exception as e:
            self.erreur = "erreur inattendue (voir le Journal)"
            self.journal(f"Mises à jour : erreur inattendue ({type(e).__name__} : {e}).")
        finally:
            if not lance:
                self.phase = "repos"
