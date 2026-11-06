"""Habillage de Djåse : icône près de l'horloge (menu), fenêtre Djåse (historique, dictionnaire,
textes rapides, style, réglages, journal), icône dessinée, démarrage avec Windows.

Tout ce qui touche aux fenêtres passe par pastille.executer() : tkinter n'aime qu'un seul fil.
Le module de dictée est reçu en paramètre (pas « import dictee » : ce serait une 2e copie).
"""

import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import winsound
from datetime import datetime
from pathlib import Path

ICI = Path(__file__).parent
from chemins import DONNEES, FIGE, PROGRAMME  # noqa: E402
ICO = PROGRAMME / "djase_nuit.ico"  # nom changé avec l'icône bleu nuit : le cache d'icônes de Windows garde l'image par nom de fichier
TEXTES = DONNEES / "textes_rapides.json"  # à part du dictionnaire : données perso, hors d'une future publication


def lire_textes():
    if not TEXTES.exists():
        return {}
    return {k: v for k, v in json.loads(TEXTES.read_text(encoding="utf-8")).items() if not k.startswith("_")}


def ecrire_textes(textes):
    texte = json.dumps(textes, ensure_ascii=False, indent=2)
    json.loads(texte)
    TEXTES.write_text(texte + "\n", encoding="utf-8")


def dernieres_lignes(fichier, n):
    """Les n dernières lignes, lues depuis la fin : la fenêtre ne relit plus tout l'historique à chaque
    dictée (disque dur ; relecture GLM du 23/10). Lève OSError si le fichier manque."""
    with open(fichier, "rb") as f:
        f.seek(0, 2)
        pos, bloc = f.tell(), b""
        while pos > 0 and bloc.count(b"\n") <= n:  # une ligne de plus : la première, coupée, est écartée
            lu = min(65536, pos)
            pos -= lu
            f.seek(pos)
            bloc = f.read(lu) + bloc
    return bloc.decode("utf-8", errors="replace").splitlines()[-n:]


PYTHONW =ICI / ".venv" / "Scripts" / "pythonw.exe"
DEMARRAGE = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Djåse.lnk"
OR = (242, 183, 5)
OR_TERNE = (150, 122, 50)
GRIS = (120, 124, 132)
NUIT = (20, 40, 80)
BLANC = (255, 255, 255)
EXEMPLE_STYLE = "Salut, tu es libre demain midi ? On se voit à 12h si ça te va."
APPLIS_COURANTES = ["whatsapp", "messenger", "discord", "telegram", "outlook", "gmail", "winword.exe"]


# ---- Icône : barres de la pastille + anneau du å, fond bleu nuit, barres d'or -----
# Choix de Manu du 23/10 (variante 3) : l'ancienne (barres blanches sur fond noir) ressemblait trop
# à celle de Wispr Flow (comparaison avec 12 applis de dictée, Captures d'écran/).

def dessiner_icone(taille=64, etat="pret", petite=None):
    """etat : pret (anneau blanc, barres or), chargement (anneau gris, or terne), pause (tout gris).
    petite : 3 barres épaisses, lisibles près de l'horloge ; sinon 4 barres fines."""
    from PIL import Image, ImageDraw
    petite = taille <= 32 if petite is None else petite
    t = taille * 4  # dessiné en grand puis réduit : bords lissés
    s = t / 64
    img = Image.new("RGBA", (t, t), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, t - 1, t - 1], radius=15 * s, fill=NUIT)
    anneau = BLANC if etat == "pret" else GRIS
    barres = {"pret": OR, "chargement": OR_TERNE}.get(etat, GRIS)
    if petite:
        cx, cy, r, ep = 32, 13, 7, 6
        rects = [(11, 31, 10, 18), (27, 24, 10, 32), (43, 31, 10, 18)]
    else:
        cx, cy, r, ep = 32, 14, 6.5, 4
        rects = [(15, 34, 5, 12), (24, 28, 5, 24), (33, 31, 5, 18), (42, 35, 5, 10)]
    d.ellipse([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s], outline=anneau, width=round(ep * s))
    for x, y, w, h in rects:
        d.rounded_rectangle([x * s, y * s, (x + w) * s, (y + h) * s], radius=w * s / 2, fill=barres)
    return img.resize((taille, taille), Image.LANCZOS)


def creer_ico():
    """djase_nuit.ico : fenêtre, raccourcis. Petites tailles en 3 barres, grandes en 4."""
    tailles = [16, 24, 32, 48, 64, 128, 256]
    images = [dessiner_icone(t) for t in tailles]
    images[-1].save(ICO, format="ICO", sizes=[(t, t) for t in tailles], append_images=images[:-1])


# ---- Raccourcis Windows (démarrage, dossier) -------------------------------------

def creer_raccourci(lien):
    """Raccourci vers Djåse (sans fenêtre noire), via WScript.Shell : pas de droits administrateur. Version assemblée :
    l'exe lui-même ; développement : pythonw.exe de l'environnement + dictee.py."""
    q = lambda p: str(p).replace("'", "''")
    if FIGE:
        cible, arguments, dossier = Path(sys.executable), "", Path(sys.executable).parent
    else:
        cible, arguments, dossier = PYTHONW, f"-X utf8 \"{q(ICI / 'dictee.py')}\"", ICI
    commande = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(lien)}');"
                f"$s.TargetPath='{q(cible)}';$s.Arguments='{arguments}';"
                f"$s.WorkingDirectory='{q(dossier)}';$s.IconLocation='{q(ICO)}';"
                f"$s.Description='Djåse, dictée locale';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", commande], capture_output=True,
                   creationflags=0x08000000)
    return lien.exists()


def demarrage_actif():
    return DEMARRAGE.exists()


def regler_demarrage(actif):
    if actif:
        return creer_raccourci(DEMARRAGE)
    DEMARRAGE.unlink(missing_ok=True)
    return False


# ---- Mémoire réellement prise (affichée dans le menu) ------------------------------

class _Memoire(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
        (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                       "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                       "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]


def _occupation(poignee):
    k = ctypes.windll.kernel32
    k.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Memoire), ctypes.c_ulong]
    m = _Memoire()
    m.cb = ctypes.sizeof(m)
    return m.WorkingSetSize if k.K32GetProcessMemoryInfo(poignee, ctypes.byref(m), m.cb) else 0


def memoire_go(nettoyeur):
    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = ctypes.c_void_p
    total = _occupation(k.GetCurrentProcess())
    if nettoyeur.proc and nettoyeur.proc.poll() is None:
        total += _occupation(int(nettoyeur.proc._handle))
    return total / 1024 ** 3


def fr(nombre):
    return f"{nombre:.1f}".replace(".", ",")


# ---- La fenêtre Djåse en page web (Chrome en mode application, Edge en secours) -----
# Servie par Djåse sur 127.0.0.1 seulement, avec une clé tirée au hasard à chaque lancement :
# sans elle, un site ouvert dans Chrome ne peut ni lire l'historique ni changer les réglages.

NAVIGATEURS = [Path(os.environ.get(v, "")) / "Google" / "Chrome" / "Application" / "chrome.exe"
               for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")] + \
              [Path(os.environ.get(v, "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
               for v in ("ProgramFiles(x86)", "ProgramFiles")]
DESCRIPTIONS_STYLE = {"formel": "Majuscules et ponctuation complète.",
                      "decontracte": "Majuscules, sans point final.",
                      "tres_decontracte": "Sans majuscules, sans virgules ni point final.",
                      "enthousiaste": "Le point final devient un point d'exclamation."}
CLES_REGLAGES = {"nettoyage", "grammaire", "historique", "audio", "style", "style_partout", "styles_applis",
                 "entree", "ecoute_apres", "double_espace", "canary_court", "oreilles", "son_messages"}
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre"]


class Serveur:
    def __init__(self, appli):
        import secrets
        from http.server import ThreadingHTTPServer
        self.appli, self.d = appli, appli.d
        self.cle = secrets.token_urlsafe(24)
        self.page_demandee = None   # page voulue depuis le menu, lue par la page à son prochain battement
        self.dernier_battement = 0.0
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), self._gestionnaire())
        self.port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def adresse(self, page):
        return f"http://127.0.0.1:{self.port}/?t={self.cle}&page={page}"

    def _gestionnaire(self):
        from http.server import BaseHTTPRequestHandler
        from urllib.parse import parse_qs, unquote, urlparse
        serveur = self

        class Gestionnaire(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _autorise(self):
                # Hôte exact (pas de « rebinding » DNS) et clé de ce lancement.
                if self.headers.get("Host") != f"127.0.0.1:{serveur.port}":
                    return False
                cle = self.headers.get("X-Djase") or parse_qs(urlparse(self.path).query).get("t", [""])[0]
                return cle == serveur.cle

            def _envoyer(self, code, corps, type_="application/json; charset=utf-8"):
                if isinstance(corps, (dict, list)):
                    corps = json.dumps(corps, ensure_ascii=False)
                if isinstance(corps, str):
                    corps = corps.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", type_)
                self.send_header("Content-Length", str(len(corps)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(corps)

            def do_GET(self):
                chemin = urlparse(self.path).path
                if chemin == "/icone.png":  # favicon : pas de clé (le navigateur ne l'envoie pas)
                    return self._envoyer(200, serveur.icone_png(), "image/png")
                if not self._autorise():
                    return self._envoyer(403, "Accès refusé", "text/plain; charset=utf-8")
                try:
                    if chemin == "/":
                        return self._envoyer(200, (PROGRAMME / "ui" / "djase.html").read_bytes(), "text/html; charset=utf-8")
                    if chemin.startswith("/audio/"):
                        nom = unquote(chemin[len("/audio/"):])
                        fichier = DONNEES / "audio" / nom
                        if "/" in nom or "\\" in nom or not nom.endswith(".wav") or not fichier.exists():
                            return self._envoyer(404, "Introuvable", "text/plain; charset=utf-8")
                        return self._envoyer(200, fichier.read_bytes(), "audio/wav")
                    reponse = serveur.lire(chemin)
                    if reponse is None:
                        return self._envoyer(404, "Introuvable", "text/plain; charset=utf-8")
                    return self._envoyer(200, reponse)
                except Exception as e:
                    serveur.d.journal(f"Fenêtre : {e}")
                    return self._envoyer(500, str(e), "text/plain; charset=utf-8")

            def do_POST(self):
                if not self._autorise():
                    return self._envoyer(403, "Accès refusé", "text/plain; charset=utf-8")
                try:
                    longueur = int(self.headers.get("Content-Length", 0))
                    donnees = json.loads(self.rfile.read(longueur) or b"{}")
                    reponse = serveur.ecrire(urlparse(self.path).path, donnees)
                    if reponse is None:
                        return self._envoyer(404, "Introuvable", "text/plain; charset=utf-8")
                    return self._envoyer(200, reponse)
                except Exception as e:
                    serveur.d.journal(f"Fenêtre : {e}")
                    return self._envoyer(500, str(e), "text/plain; charset=utf-8")
        return Gestionnaire

    def icone_png(self):
        import io
        tampon = io.BytesIO()
        dessiner_icone(64, "pret").save(tampon, format="PNG")
        return tampon.getvalue()

    # -- lectures --

    def lire(self, chemin):
        import time
        d = self.d
        if chemin == "/api/etat":
            self.dernier_battement = time.time()
            self.appli.retenir_fenetre()  # toutes les 2 s : position et taille, pour la prochaine ouverture
            page, self.page_demandee = self.page_demandee, None
            fichier = DONNEES / "historique.jsonl"  # date de l'historique : la page se recharge quand il change
            return {"texte": self.appli.texte_etat(), "page": page, **self.appli.etat_details(),
                    "historique": fichier.stat().st_mtime if fichier.exists() else 0}
        if chemin == "/api/historique":
            return self.historique()
        if chemin == "/api/dictionnaire":
            return self.dictionnaire()
        if chemin == "/api/reglages":
            return self.reglages()
        if chemin == "/api/journal":
            return list(d.JOURNAL)
        return None

    def historique(self):
        d = self.d
        try:
            lignes = dernieres_lignes(DONNEES / "historique.jsonl", 500)
        except OSError:
            return []
        sortie = []
        for l in reversed(lignes):
            try:
                e = json.loads(l)
                date = datetime.fromisoformat(e["date"])
            except (ValueError, KeyError):
                continue
            if not e.get("texte"):
                continue  # dictée vide : gardée pour l'analyse (audio), pas affichée
            brut = " ".join(m.get("brut", "") for m in e.get("morceaux", []))
            # « Entendu » seulement si les MOTS diffèrent (pas pour un point ou une majuscule).
            mots = lambda t: re.findall(r"\w+", t.lower())
            if mots(brut) == mots(e.get("texte", "")):
                brut = ""
            audio = e.get("audio")
            sortie.append({
                "texte": e.get("texte", ""), "heure": date.strftime("%H:%M"),
                "jour": f"{JOURS[date.weekday()]} {date.day} {MOIS[date.month - 1]}",
                "entendu": brut if brut and brut != e.get("texte") and not e.get("correction") else "",
                "correction": e.get("correction"), "grammaire": e.get("grammaire") or [],
                "appli": e.get("appli") or "", "style": e.get("style") or "formel",
                "style_nom": d.STYLES.get(e.get("style") or "formel", "").lower(),
                "audio": audio if audio and (DONNEES / "audio" / audio).exists() else None})
        return sortie

    def dictionnaire(self):
        donnees = json.loads((DONNEES / "dictionnaire.json").read_text(encoding="utf-8"))
        return {"mots": donnees.get("mots", []), "remplacements": donnees.get("remplacements", {}),
                "textes_rapides": lire_textes(), "par_appli": donnees.get("par_appli", {}),
                "applis": self.applis_vues(),
                "motifs": len([m for m in donnees.get("remplacements_motifs", {}) if not m.startswith("_")])}

    def applis_vues(self):
        """Programmes où Manu a déjà dicté, puis les applis courantes : propositions des listes."""
        vues = set()
        try:
            for l in dernieres_lignes(DONNEES / "historique.jsonl", 300):
                appli = json.loads(l).get("appli")
                if appli:
                    vues.add(appli)
        except (OSError, ValueError):
            pass
        return sorted(vues) + [a for a in APPLIS_COURANTES if a not in vues]

    def reglages(self):
        d = self.d
        return {**{c: d.REGLAGES[c] for c in CLES_REGLAGES}, "demarrage": demarrage_actif(),
                "styles": {k: {"nom": nom, "desc": DESCRIPTIONS_STYLE[k],
                               "exemple": d.appliquer_style(EXEMPLE_STYLE, k)} for k, nom in d.STYLES.items()},
                "applis_vues": self.applis_vues()}

    # -- écritures --

    def ecrire(self, chemin, donnees):
        d = self.d
        if chemin == "/api/copier":
            import pyperclip
            pyperclip.copy(str(donnees.get("texte", "")))
            return {"ok": True}
        if chemin == "/api/dictionnaire":
            fichier = DONNEES / "dictionnaire.json"
            actuel = json.loads(fichier.read_text(encoding="utf-8"))
            if isinstance(donnees.get("mots"), list):
                actuel["mots"] = [str(m).strip() for m in donnees["mots"] if str(m).strip()]
            if isinstance(donnees.get("remplacements"), dict):
                actuel["remplacements"] = {str(a).strip(): str(b) for a, b in donnees["remplacements"].items()
                                           if str(a).strip()}
            if isinstance(donnees.get("par_appli"), dict):
                propre = {}
                for appli, vocab in donnees["par_appli"].items():
                    appli = str(appli).strip().lower()
                    if not appli or not isinstance(vocab, dict):
                        continue
                    mots = [str(m).strip() for m in vocab.get("mots", []) if str(m).strip()]
                    rempl = {str(a).strip(): str(b) for a, b in dict(vocab.get("remplacements", {})).items()
                             if str(a).strip()}
                    if mots or rempl:  # appli vidée : retirée du fichier
                        propre[appli] = {"mots": mots, "remplacements": rempl}
                actuel["par_appli"] = propre
            if isinstance(donnees.get("textes_rapides"), dict):
                ecrire_textes({str(a).strip(): str(b) for a, b in donnees["textes_rapides"].items() if str(a).strip()})
            texte = json.dumps(actuel, ensure_ascii=False, indent=2)
            json.loads(texte)
            fichier.write_text(texte + "\n", encoding="utf-8")
            return self.dictionnaire()
        if chemin == "/api/reglages":
            if "demarrage" in donnees:
                regler_demarrage(bool(donnees["demarrage"]))
            a_sauver = []
            for cle, valeur in donnees.items():
                if cle not in CLES_REGLAGES:
                    continue
                if cle == "style" and valeur not in d.STYLES:
                    continue
                if cle == "styles_applis":
                    valeur = {str(a).strip().lower(): s for a, s in dict(valeur).items()
                              if str(a).strip() and s in d.STYLES}
                elif cle == "entree":
                    valeur = "auto" if valeur == "auto" else int(valeur)
                elif cle == "ecoute_apres":
                    valeur = max(0.0, min(1.0, float(valeur)))
                elif cle in ("nettoyage", "grammaire", "historique", "audio", "style_partout", "double_espace",
                             "canary_court", "oreilles", "son_messages"):
                    valeur = bool(valeur)
                d.REGLAGES[cle] = valeur
                a_sauver.append(cle)
            if a_sauver:
                sauver_reglages(d, a_sauver)
            return self.reglages()
        return None


def sauver_reglages(d, cles):
    fichier = DONNEES / "reglages.json"
    try:
        existants = json.loads(fichier.read_text(encoding="utf-8")) if fichier.exists() else {}
    except ValueError:
        existants = {}
    existants.update({c: d.REGLAGES[c] for c in cles})
    fichier.write_text(json.dumps(existants, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def trouver_fenetre():
    """La fenêtre Djåse (Chrome en mode application, titre « Djåse »), ou None."""
    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = ctypes.c_void_p
    return user32.FindWindowW("Chrome_WidgetWin_1", "Djåse")


def fenetre_ouverte():
    """La fenêtre Djåse déjà ouverte : on la ramène devant au lieu d'en ouvrir une 2e."""
    user32 = ctypes.windll.user32
    hwnd = trouver_fenetre()
    if hwnd:
        # Réduite : on la rouvre ; agrandie, elle le reste (SW_RESTORE la remettait en petit).
        user32.ShowWindow(ctypes.c_void_p(hwnd), 9 if user32.IsIconic(ctypes.c_void_p(hwnd)) else 5)
        user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
    return bool(hwnd)


# ---- Position de la fenêtre, retenue d'une ouverture à l'autre (demande de Manu du 23/10) -----
# Par Windows et non par Chrome, qui retient mal la taille d'une fenêtre « appli ». Coordonnées
# de GetWindowPlacement (relatives à la zone de travail) : relues et reposées telles quelles.

class _Placement(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint), ("flags", ctypes.c_uint), ("showCmd", ctypes.c_uint),
                ("ptMin", ctypes.c_long * 2), ("ptMax", ctypes.c_long * 2), ("rect", ctypes.c_long * 4)]


def zone_de_travail():
    """L'écran sans la barre des tâches : (gauche, haut, droite, bas)."""
    r = (ctypes.c_long * 4)()
    ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return tuple(r)


def placement_par_defaut():
    """Première ouverture : moitié gauche de l'écran, toute la hauteur (choix de Manu)."""
    g, h, d, b = zone_de_travail()
    return {"max": False, "rect": [0, 0, (d - g) // 2, b - h]}


def lire_placement(hwnd):
    p = _Placement()
    p.length = ctypes.sizeof(p)
    if not ctypes.windll.user32.GetWindowPlacement(ctypes.c_void_p(hwnd), ctypes.byref(p)):
        return None
    if p.showCmd == 2:  # réduite dans la barre des tâches : on garde la position d'avant
        return None
    return {"max": p.showCmd == 3, "rect": list(p.rect)}


def poser_placement(hwnd, place):
    p = _Placement()
    p.length = ctypes.sizeof(p)
    p.showCmd = 3 if place.get("max") else 1  # SW_MAXIMIZE / SW_SHOWNORMAL
    for i, v in enumerate(place["rect"]):
        p.rect[i] = int(v)
    ctypes.windll.user32.SetWindowPlacement(ctypes.c_void_p(hwnd), ctypes.byref(p))


def fermer_fenetres():
    """Quitter : Chrome fait vivre la fenêtre Djåse, elle survivrait à Djåse. On les ferme toutes."""
    user32 = ctypes.windll.user32
    trouvees = []
    classe, titre = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(64)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def voir(hwnd, _):
        user32.GetClassNameW(ctypes.c_void_p(hwnd), classe, 64)
        user32.GetWindowTextW(ctypes.c_void_p(hwnd), titre, 64)
        if classe.value == "Chrome_WidgetWin_1" and titre.value == "Djåse":
            trouvees.append(hwnd)
        return True
    user32.EnumWindows(voir, None)
    for hwnd in trouvees:
        user32.PostMessageW(ctypes.c_void_p(hwnd), 0x0010, 0, 0)  # WM_CLOSE
    return len(trouvees)


# ---- L'appli : icône près de l'horloge et son menu ---------------------------------

class Appli:
    def __init__(self, d, pastille, transcripteur, nettoyeur):
        self.d = d
        self.pastille = pastille
        self.transcripteur = transcripteur
        self.nettoyeur = nettoyeur
        self.fenetre = None
        self.serveur = None  # démarré à la première ouverture de la fenêtre
        self.icone = None
        self.etat = None

    def demarrer(self):
        import pystray
        if not ICO.exists():
            creer_ico()
        lien = ICI / "Djåse.lnk"  # pour lancer Djåse d'un double-clic depuis le dossier (développement seulement :
        if not FIGE and not lien.exists():  # installé, c'est l'installateur qui pose les raccourcis)
            creer_raccourci(lien)
        I, M = pystray.MenuItem, pystray.Menu
        menu = M(
            I(lambda _: self.texte_etat(), None, enabled=False),
            I("Ouvrir Djåse", lambda: self.ouvrir("historique"), default=True),
            M.SEPARATOR,
            I("Copier la dernière dictée", self.copier_derniere),
            I(lambda _: "Reprendre" if self.d.ETAT["pause"] else "Mettre en pause", self.basculer_pause),
            M.SEPARATOR,
            I("Historique…", lambda: self.ouvrir("historique")),
            I("Dictionnaire…", lambda: self.ouvrir("dictionnaire")),
            I("Textes rapides…", lambda: self.ouvrir("textes")),
            I("Style…", lambda: self.ouvrir("style")),
            I("Réglages…", lambda: self.ouvrir("reglages")),
            M.SEPARATOR,
            I("Démarrer avec Windows", lambda: regler_demarrage(not demarrage_actif()),
              checked=lambda _: demarrage_actif()),
            I("Ouvrir le dossier", lambda: os.startfile(DONNEES)),
            I("Quitter Djåse", self.quitter),
        )
        self.icone = pystray.Icon("Djase", dessiner_icone(64, "chargement", petite=True), "Djåse · chargement…", menu)
        threading.Thread(target=self.icone.run, daemon=True).start()
        threading.Thread(target=self._surveiller, daemon=True).start()

    def etat_actuel(self):
        if self.d.ETAT["pause"]:
            return "pause"
        return "pret" if self.d.ETAT.get("tout_pret") else "chargement"  # comme le message « Prêt »

    def texte_etat(self):
        etat = self.etat_actuel()
        if getattr(self.transcripteur, "erreur", None):
            return "Djåse · moteur de dictée en panne (voir le Journal)"
        if etat == "chargement":
            return "Djåse · chargement…"
        if etat == "pause":
            return f"Djåse · en pause · {fr(memoire_go(self.nettoyeur))} Go"
        qwen = "Qwen prêt" if self.nettoyeur.pret.is_set() else (
            "Qwen désactivé" if not self.d.REGLAGES["nettoyage"] else "Qwen en chargement")
        return f"Djåse · prêt · {qwen} · {fr(memoire_go(self.nettoyeur))} Go"

    def etat_details(self):
        """Pour l'en-tête de la fenêtre : badge d'état et ligne de détails."""
        d = self.d
        qwen = "prêt" if self.nettoyeur.pret.is_set() else (
            "désactivé" if not d.REGLAGES["nettoyage"] else "en pause" if d.ETAT["pause"] else "en chargement")
        canary = "prêt" if getattr(self.transcripteur, "canary", None) is not None else (
            "désactivé" if not d.REGLAGES.get("canary_court") else "en chargement")
        return {"etat": self.etat_actuel(), "memoire": f"{fr(memoire_go(self.nettoyeur))} Go",
                "qwen": qwen, "canary": canary}

    def _surveiller(self):
        import time
        tour = 0
        while self.icone:
            etat = self.etat_actuel()
            if etat != self.etat:
                self.etat = etat
                self.icone.icon = dessiner_icone(64, etat, petite=True)
                self.icone.title = {"pret": "Djåse · prêt", "chargement": "Djåse · chargement…",
                                    "pause": "Djåse · en pause"}[etat]
            if tour % 3 == 0:
                try:
                    self.icone.update_menu()  # la ligne du haut (mémoire) se recalcule
                except Exception:
                    pass
            tour += 1
            time.sleep(2)

    def copier_derniere(self):
        import pyperclip
        if self.d.DERNIERE[0]:
            pyperclip.copy(self.d.DERNIERE[0])
            self.pastille.message("Copiée · Ctrl+V pour coller")
        else:
            self.pastille.message("Aucune dictée pour l'instant", alerte=True)

    def basculer_pause(self):
        d = self.d
        d.ETAT["pause"] = not d.ETAT["pause"]
        if d.ETAT["pause"]:
            d.journal("En pause : Ctrl+Win ne dicte plus, Qwen libère sa mémoire.")
            self.pastille.message("Djåse en pause")
            threading.Thread(target=self.nettoyeur.arreter, daemon=True).start()
        else:
            d.journal("Reprise : Qwen se recharge.")
            self.pastille.message("Djåse reprend")
            threading.Thread(target=self.nettoyeur.demarrer, daemon=True).start()

    def ouvrir(self, page="historique"):
        """Depuis n'importe quel fil : la page web dans Chrome (ou Edge), sinon la fenêtre tkinter."""
        threading.Thread(target=self._ouvrir_web, args=(page,), daemon=True).start()

    def _ouvrir_web(self, page):
        import time
        navigateur = next((n for n in NAVIGATEURS if n.is_file()), None)
        if navigateur is None:
            self.pastille.executer(lambda: self.ouvrir_fenetre(page))
            return
        if self.serveur is None:
            self.serveur = Serveur(self)
        # Déjà ouverte (elle bat toutes les 2 s) : on la ramène devant, sur la page voulue.
        if time.time() - self.serveur.dernier_battement < 5 and fenetre_ouverte():
            self.serveur.page_demandee = page
            return
        place = self.d.REGLAGES.get("fenetre") or placement_par_defaut()
        g, h, _, _ = zone_de_travail()
        x0, y0, x1, y1 = place["rect"]
        # Les options de Chrome placent la fenêtre dès son apparition (pas de saut) ; Windows corrige
        # ensuite si Chrome les a ignorées (il le fait quand il tourne déjà).
        options = [f"--window-position={x0 + g},{y0 + h}", f"--window-size={x1 - x0},{y1 - y0}"]
        self.en_placement = True  # pas d'enregistrement de la position provisoire de Chrome
        try:
            subprocess.Popen([str(navigateur), f"--app={self.serveur.adresse(page)}", *options]
                             + (["--start-maximized"] if place.get("max") else []))
            for _ in range(50):  # la fenêtre prend son titre « Djåse » au chargement de la page
                time.sleep(0.2)
                hwnd = trouver_fenetre()
                if hwnd:
                    poser_placement(hwnd, place)
                    self.place_vue = place
                    break
        finally:
            self.en_placement = False

    def retenir_fenetre(self):
        """Appelé à chaque battement de la page : enregistre la position si elle a changé."""
        if getattr(self, "en_placement", False):
            return
        hwnd = trouver_fenetre()
        place = lire_placement(hwnd) if hwnd else None
        if place and place != getattr(self, "place_vue", None):
            self.place_vue = place
            self.d.REGLAGES["fenetre"] = place
            sauver_reglages(self.d, ["fenetre"])

    def ouvrir_fenetre(self, page="historique"):
        """Fenêtre tkinter de secours (aucun navigateur trouvé). Dans le fil de tkinter seulement."""
        if self.fenetre and self.fenetre.existe():
            self.fenetre.montrer(page)
        else:
            self.fenetre = Fenetre(self, page)

    def quitter(self):
        self.d.journal("Quitter (menu de l'icône).")
        icone, self.icone = self.icone, None
        fermer_fenetres()
        icone.stop()
        self.pastille.executer(self.pastille.root.quit)


# ---- La fenêtre Djåse ----------------------------------------------------------------

class Fenetre:
    PAGES = [("historique", "Historique"), ("dictionnaire", "Dictionnaire"), ("textes", "Textes rapides"),
             ("style", "Style"), ("reglages", "Réglages"), ("journal", "Journal")]
    theme_pose = False
    SOMBRE, CLAIR, DISCRET = "#1c1c1c", "#fafafa", "#9a9ea8"

    def __init__(self, appli, page):
        import tkinter as tk
        from tkinter import ttk
        import sv_ttk
        self.tk, self.ttk = tk, ttk
        self.appli, self.d = appli, appli.d
        self.top = tk.Toplevel(appli.pastille.root)
        if not Fenetre.theme_pose:
            sv_ttk.set_theme("dark", appli.pastille.root)
            # Fonds et textes du thème : posés d'ordinaire à l'événement <<ThemeChanged>>, qui
            # n'arrive qu'après la construction de la fenêtre (cadres restés clairs) : appel direct.
            appli.pastille.root.tk.call("configure_colors")
            Fenetre.theme_pose = True
        self.top.title("Djåse")
        self.top.configure(bg=self.SOMBRE)
        try:
            self.top.iconbitmap(str(ICO))
        except Exception:
            pass
        self.top.geometry("900x580")
        self.top.minsize(700, 440)
        self.top.protocol("WM_DELETE_WINDOW", self.fermer)
        nav = ttk.Frame(self.top, padding=(10, 12))
        nav.pack(side="left", fill="y")
        self.boutons = {}
        for cle, nom in self.PAGES:
            b = ttk.Button(nav, text=nom, width=16, command=lambda c=cle: self.montrer(c))
            b.pack(fill="x", pady=2)
            self.boutons[cle] = b
        self.contenu = ttk.Frame(self.top, padding=(14, 12))
        self.contenu.pack(side="left", fill="both", expand=True)
        self.montrer(page)

    def existe(self):
        try:
            return bool(self.top.winfo_exists())
        except Exception:
            return False

    def fermer(self):
        self.top.destroy()
        self.appli.fenetre = None

    def montrer(self, page):
        for cle, b in self.boutons.items():
            b.configure(style="Accent.TButton" if cle == page else "TButton")
        for enfant in self.contenu.winfo_children():
            enfant.destroy()
        self.page = page
        getattr(self, f"page_{page}")(self.contenu)
        self.top.deiconify()
        self.top.lift()
        self.top.focus_force()

    # -- outils communs --

    def titre(self, cadre, texte, aide=None):
        ttk = self.ttk
        ttk.Label(cadre, text=texte, font=("Segoe UI Semibold", 15)).pack(anchor="w")
        if aide:
            ttk.Label(cadre, text=aide, foreground=self.DISCRET, wraplength=640, justify="left").pack(anchor="w", pady=(2, 10))

    def tableau(self, cadre, colonnes, largeurs, hauteur=12):
        ttk = self.ttk
        bloc = ttk.Frame(cadre)
        bloc.pack(fill="both", expand=True)
        arbre = ttk.Treeview(bloc, columns=[c for c, _ in colonnes], show="headings", height=hauteur)
        for (cle, nom), largeur in zip(colonnes, largeurs):
            arbre.heading(cle, text=nom, anchor="w")
            arbre.column(cle, width=largeur, anchor="w", stretch=largeur > 200)
        defil = ttk.Scrollbar(bloc, orient="vertical", command=arbre.yview)
        arbre.configure(yscrollcommand=defil.set)
        arbre.pack(side="left", fill="both", expand=True)
        defil.pack(side="right", fill="y")
        return arbre

    def rangee(self, cadre):
        r = self.ttk.Frame(cadre)
        r.pack(fill="x", pady=(8, 0))
        return r

    def lire_dico(self):
        return json.loads((DONNEES / "dictionnaire.json").read_text(encoding="utf-8"))

    def ecrire_dico(self, donnees):
        texte = json.dumps(donnees, ensure_ascii=False, indent=2)
        json.loads(texte)
        (DONNEES / "dictionnaire.json").write_text(texte + "\n", encoding="utf-8")

    def sauver_reglages(self, *cles):
        fichier = DONNEES / "reglages.json"
        try:
            existants = json.loads(fichier.read_text(encoding="utf-8")) if fichier.exists() else {}
        except ValueError:
            existants = {}
        existants.update({c: self.d.REGLAGES[c] for c in cles})
        fichier.write_text(json.dumps(existants, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def lire_historique(self, nombre=500):
        try:
            lignes = dernieres_lignes(DONNEES / "historique.jsonl", nombre)
        except OSError:
            return []
        entrees = []
        for l in reversed(lignes):
            try:
                entrees.append(json.loads(l))
            except ValueError:
                pass
        return entrees

    # -- pages --

    def page_historique(self, cadre):
        tk, ttk = self.tk, self.ttk
        self.titre(cadre, "Historique")
        recherche = tk.StringVar()
        ligne = ttk.Frame(cadre)
        ligne.pack(fill="x", pady=(4, 8))
        ttk.Label(ligne, text="Rechercher").pack(side="left", padx=(0, 8))
        ttk.Entry(ligne, textvariable=recherche).pack(side="left", fill="x", expand=True)
        arbre = self.tableau(cadre, [("quand", "Quand"), ("texte", "Texte")], [110, 600], hauteur=14)
        detail = ttk.Label(cadre, text="", foreground=self.DISCRET, wraplength=660, justify="left")
        detail.pack(anchor="w", pady=(6, 0))
        entrees = self.lire_historique()
        vues = {}

        def remplir(*_):
            arbre.delete(*arbre.get_children())
            vues.clear()
            filtre = recherche.get().lower()
            for i, e in enumerate(entrees):
                texte = e.get("texte", "")
                if filtre and filtre not in texte.lower():
                    continue
                try:
                    quand = datetime.fromisoformat(e["date"]).strftime("%d/%m %H:%M")
                except (KeyError, ValueError):
                    quand = ""
                vues[arbre.insert("", "end", values=(quand, texte.replace("\n", " ⏎ ")))] = e
        recherche.trace_add("write", remplir)
        remplir()

        def choisie():
            sel = arbre.selection()
            return vues.get(sel[0]) if sel else None

        def montrer_detail(_=None):
            e = choisie()
            if not e:
                return
            if e.get("correction"):
                a, b = e["correction"]
                detail.configure(text=f"Corrigé à la voix : {a} → {b}")
            else:
                brut = " ".join(m.get("brut", "") for m in e.get("morceaux", []))
                autres = [f"Entendu : « {brut} »"] if brut and brut != e.get("texte") else []
                if e.get("grammaire"):
                    autres.append("Grammaire : " + ", ".join(f"{x} → {y}" for x, y in e["grammaire"]))
                detail.configure(text="\n".join(autres))
        arbre.bind("<<TreeviewSelect>>", montrer_detail)

        def copier():
            e = choisie()
            if e:
                self.top.clipboard_clear()
                self.top.clipboard_append(e.get("texte", ""))
                self.appli.pastille.message("Copiée · Ctrl+V pour coller")

        def ecouter():
            e = choisie()
            audio = DONNEES / "audio" / e["audio"] if e and e.get("audio") else None
            if audio and audio.exists():
                winsound.PlaySound(str(audio), winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                detail.configure(text="Pas d'audio gardé pour cette dictée.")
        r = self.rangee(cadre)
        ttk.Button(r, text="Copier", command=copier).pack(side="left")
        ttk.Button(r, text="▶ Écouter", command=ecouter).pack(side="left", padx=6)
        ttk.Button(r, text="■ Arrêter", command=lambda: winsound.PlaySound(None, 0)).pack(side="left")

    def page_dictionnaire(self, cadre):
        tk, ttk = self.tk, self.ttk
        self.titre(cadre, "Dictionnaire", "Mots : écrits toujours ainsi (MemCore, GitHub…). "
                   "Remplacements : ce que Djåse entend de travers, et ce qu'il doit écrire. "
                   "Pris en compte dès la dictée suivante.")
        donnees = self.lire_dico()
        cotes = ttk.Frame(cadre)
        cotes.pack(fill="both", expand=True)
        gauche, droite = ttk.Frame(cotes), ttk.Frame(cotes)
        gauche.pack(side="left", fill="both", expand=False, padx=(0, 14))
        droite.pack(side="left", fill="both", expand=True)

        ttk.Label(gauche, text="Mots", font=("Segoe UI Semibold", 11)).pack(anchor="w")
        mots = self.tableau(gauche, [("mot", "Mot")], [180], hauteur=11)
        for m in donnees.get("mots", []):
            mots.insert("", "end", values=(m,))
        nouveau_mot = tk.StringVar()
        r = self.rangee(gauche)
        ttk.Entry(r, textvariable=nouveau_mot, width=16).pack(side="left")

        def ajouter_mot():
            m = nouveau_mot.get().strip()
            if m and m not in donnees["mots"]:
                donnees["mots"].append(m)
                self.ecrire_dico(donnees)
                mots.insert("", "end", values=(m,))
                nouveau_mot.set("")

        def supprimer_mot():
            for sel in mots.selection():
                m = mots.item(sel, "values")[0]
                if m in donnees["mots"]:
                    donnees["mots"].remove(m)
                mots.delete(sel)
            self.ecrire_dico(donnees)
        ttk.Button(r, text="Ajouter", command=ajouter_mot).pack(side="left", padx=4)
        ttk.Button(r, text="Supprimer", command=supprimer_mot).pack(side="left")

        ttk.Label(droite, text="Remplacements", font=("Segoe UI Semibold", 11)).pack(anchor="w")
        rempl = self.tableau(droite, [("entendu", "Entendu"), ("ecrit", "Écrit")], [220, 220], hauteur=11)
        for a, b in donnees.get("remplacements", {}).items():
            rempl.insert("", "end", values=(a, b))
        entendu, ecrit = tk.StringVar(), tk.StringVar()
        r = self.rangee(droite)
        ttk.Entry(r, textvariable=entendu, width=13).pack(side="left")
        ttk.Label(r, text="→").pack(side="left", padx=4)
        ttk.Entry(r, textvariable=ecrit, width=13).pack(side="left")

        def choisir_rempl(_=None):
            sel = rempl.selection()
            if sel:
                a, b = rempl.item(sel[0], "values")
                entendu.set(a)
                ecrit.set(b)
        rempl.bind("<<TreeviewSelect>>", choisir_rempl)

        def ajouter_rempl():
            a, b = entendu.get().strip(), ecrit.get().strip()
            if a and b:
                donnees.setdefault("remplacements", {})[a] = b
                self.ecrire_dico(donnees)
                for sel in rempl.get_children():
                    if rempl.item(sel, "values")[0] == a:
                        rempl.delete(sel)
                rempl.insert("", "end", values=(a, b))
                entendu.set("")
                ecrit.set("")

        def supprimer_rempl():
            for sel in rempl.selection():
                donnees["remplacements"].pop(rempl.item(sel, "values")[0], None)
                rempl.delete(sel)
            self.ecrire_dico(donnees)
        ttk.Button(r, text="Ajouter", command=ajouter_rempl).pack(side="left", padx=4)
        ttk.Button(r, text="Supprimer", command=supprimer_rempl).pack(side="left")
        n = len([m for m in donnees.get("remplacements_motifs", {}) if not m.startswith("_")])
        ttk.Label(cadre, text=f"{n} règles avancées (expressions régulières) : gardées telles quelles, "
                              "modifiables seulement dans dictionnaire.json.",
                  foreground=self.DISCRET).pack(anchor="w", pady=(10, 0))

    def page_textes(self, cadre):
        tk, ttk = self.tk, self.ttk
        self.titre(cadre, "Textes rapides", "Ce que vous dites est remplacé par le texte, n'importe où dans une "
                   "dictée. Choisissez une formule que vous ne dites pas par hasard, par exemple "
                   "« insère mon GitHub » plutôt que « mon GitHub ».")
        textes = lire_textes()
        arbre = self.tableau(cadre, [("dis", "Ce que vous dites"), ("colle", "Ce qui est collé")], [230, 430], hauteur=10)
        for a, b in textes.items():
            arbre.insert("", "end", values=(a, b))
        dis, colle = tk.StringVar(), tk.StringVar()
        grille = ttk.Frame(cadre)
        grille.pack(fill="x", pady=(10, 0))
        ttk.Label(grille, text="Ce que vous dites").grid(row=0, column=0, sticky="w")
        ttk.Entry(grille, textvariable=dis).grid(row=1, column=0, sticky="we", padx=(0, 8))
        ttk.Label(grille, text="Ce qui est collé").grid(row=0, column=1, sticky="w")
        ttk.Entry(grille, textvariable=colle).grid(row=1, column=1, sticky="we")
        grille.columnconfigure(0, weight=1)
        grille.columnconfigure(1, weight=2)

        def choisir(_=None):
            sel = arbre.selection()
            if sel:
                a, b = arbre.item(sel[0], "values")
                dis.set(a)
                colle.set(b)
        arbre.bind("<<TreeviewSelect>>", choisir)

        def enregistrer():
            a, b = dis.get().strip(), colle.get().strip()
            if not (a and b):
                return
            textes[a] = b
            ecrire_textes(textes)
            for sel in arbre.get_children():
                if arbre.item(sel, "values")[0] == a:
                    arbre.delete(sel)
            arbre.insert("", "end", values=(a, b))
            dis.set("")
            colle.set("")

        def supprimer():
            for sel in arbre.selection():
                textes.pop(arbre.item(sel, "values")[0], None)
                arbre.delete(sel)
            ecrire_textes(textes)
            dis.set("")
            colle.set("")
        r = self.rangee(cadre)
        ttk.Button(r, text="Enregistrer", style="Accent.TButton", command=enregistrer).pack(side="left")
        ttk.Button(r, text="Supprimer", command=supprimer).pack(side="left", padx=6)

    def page_style(self, cadre):
        tk, ttk = self.tk, self.ttk
        d = self.d
        self.titre(cadre, "Style", "Le style par défaut, et des exceptions par application : un nom de "
                   "programme (winword.exe) ou un mot du titre de la fenêtre (« whatsapp » vaut pour "
                   "l'appli comme pour l'onglet de Chrome).")
        noms = list(d.STYLES.values())
        cle_de = {v: k for k, v in d.STYLES.items()}
        haut = ttk.Frame(cadre)
        haut.pack(fill="x")
        ttk.Label(haut, text="Style par défaut").pack(side="left")
        defaut = tk.StringVar(value=d.STYLES.get(d.REGLAGES["style"], "Formel"))
        ttk.Combobox(haut, textvariable=defaut, values=noms, state="readonly", width=18).pack(side="left", padx=8)
        apercu = ttk.Label(cadre, text="", foreground=self.DISCRET, wraplength=640, justify="left")
        apercu.pack(anchor="w", pady=(8, 4))

        def changer_defaut(*_):
            d.REGLAGES["style"] = cle_de[defaut.get()]
            self.sauver_reglages("style")
            apercu.configure(text="Exemple : " + d.appliquer_style(EXEMPLE_STYLE, d.REGLAGES["style"]))
        defaut.trace_add("write", changer_defaut)
        apercu.configure(text="Exemple : " + d.appliquer_style(EXEMPLE_STYLE, d.REGLAGES["style"]))

        partout = tk.BooleanVar(value=d.REGLAGES["style_partout"])

        def changer_partout():
            d.REGLAGES["style_partout"] = partout.get()
            self.sauver_reglages("style_partout")
        ttk.Checkbutton(cadre, text="Même style partout (les exceptions ci-dessous sont ignorées)",
                        variable=partout, command=changer_partout).pack(anchor="w", pady=(4, 10))

        arbre = self.tableau(cadre, [("appli", "Application"), ("style", "Style")], [260, 200], hauteur=7)
        for a, s in d.REGLAGES["styles_applis"].items():
            arbre.insert("", "end", values=(a, d.STYLES.get(s, s)))
        vues = sorted({e.get("appli") for e in self.lire_historique(300) if e.get("appli")})
        appli, style = tk.StringVar(), tk.StringVar(value="Décontracté")
        r = self.rangee(cadre)
        ttk.Combobox(r, textvariable=appli, values=vues + [a for a in APPLIS_COURANTES if a not in vues],
                     width=22).pack(side="left")
        ttk.Combobox(r, textvariable=style, values=noms, state="readonly", width=16).pack(side="left", padx=6)

        def choisir(_=None):  # clic sur une ligne : ses valeurs dans les champs, pour la modifier
            sel = arbre.selection()
            if sel:
                a, s = arbre.item(sel[0], "values")
                appli.set(a)
                style.set(s)
        arbre.bind("<<TreeviewSelect>>", choisir)

        def ajouter():
            a = appli.get().strip().lower()
            if not a:
                return
            d.REGLAGES["styles_applis"] = {**d.REGLAGES["styles_applis"], a: cle_de[style.get()]}
            self.sauver_reglages("styles_applis")
            for sel in arbre.get_children():
                if arbre.item(sel, "values")[0] == a:
                    arbre.delete(sel)
            arbre.insert("", "end", values=(a, style.get()))
            appli.set("")

        def supprimer():
            applis = dict(d.REGLAGES["styles_applis"])
            for sel in arbre.selection():
                applis.pop(arbre.item(sel, "values")[0], None)
                arbre.delete(sel)
            d.REGLAGES["styles_applis"] = applis
            self.sauver_reglages("styles_applis")
        ttk.Button(r, text="Enregistrer", style="Accent.TButton", command=ajouter).pack(side="left")
        ttk.Button(r, text="Supprimer", command=supprimer).pack(side="left", padx=6)
        ttk.Label(cadre, text="Cliquez sur une ligne pour changer son style, puis Enregistrer. "
                              "Dans la liste : les programmes où vous avez déjà dicté.",
                  foreground=self.DISCRET).pack(anchor="w", pady=(6, 0))

    def page_reglages(self, cadre):
        tk, ttk = self.tk, self.ttk
        d = self.d
        self.titre(cadre, "Réglages", "Ceux marqués d'une étoile prennent effet au prochain lancement de Djåse.")
        cases = [("nettoyage", "Nettoyage par Qwen (bah, ben, « non, plutôt »…) *"),
                 ("grammaire", "Grammaire : accords et conjugaisons (Grammalecte) *"),
                 ("historique", "Garder l'historique des dictées"),
                 ("audio", "Garder l'audio de chaque dictée (pour réécouter une erreur)")]
        variables = {}
        for cle, texte in cases:
            variables[cle] = tk.BooleanVar(value=bool(d.REGLAGES[cle]))

            def changer(c=cle):
                d.REGLAGES[c] = variables[c].get()
                self.sauver_reglages(c)
            ttk.Checkbutton(cadre, text=texte, variable=variables[cle], command=changer).pack(anchor="w", pady=3)
        demarrage = tk.BooleanVar(value=demarrage_actif())
        ttk.Checkbutton(cadre, text="Démarrer avec Windows", variable=demarrage,
                        command=lambda: demarrage.set(regler_demarrage(demarrage.get()))).pack(anchor="w", pady=3)

        grille = ttk.Frame(cadre)
        grille.pack(anchor="w", pady=(14, 0))
        ttk.Label(grille, text="Voie du micro").grid(row=0, column=0, sticky="w", pady=4)
        voie = tk.StringVar(value=str(d.REGLAGES["entree"]))
        ttk.Combobox(grille, textvariable=voie, values=["auto", "1", "2"], state="readonly", width=8).grid(
            row=0, column=1, sticky="w", padx=10)

        def changer_voie(*_):
            d.REGLAGES["entree"] = voie.get() if voie.get() == "auto" else int(voie.get())
            self.sauver_reglages("entree")
        voie.trace_add("write", changer_voie)
        ttk.Label(grille, text="Écoute après le relâchement (s)").grid(row=1, column=0, sticky="w", pady=4)
        ecoute = tk.StringVar(value=str(d.REGLAGES["ecoute_apres"]))
        ttk.Spinbox(grille, textvariable=ecoute, from_=0.0, to=1.0, increment=0.1, width=8,
                    format="%.1f").grid(row=1, column=1, sticky="w", padx=10)

        def changer_ecoute(*_):
            try:
                d.REGLAGES["ecoute_apres"] = max(0.0, min(1.0, float(ecoute.get().replace(",", "."))))
                self.sauver_reglages("ecoute_apres")
            except ValueError:
                pass
        ecoute.trace_add("write", changer_ecoute)
        r = self.rangee(cadre)
        r.pack_configure(pady=(18, 0))
        ttk.Button(r, text="Ouvrir le dossier de Djåse", command=lambda: os.startfile(DONNEES)).pack(side="left")

    def page_journal(self, cadre):
        tk, ttk = self.tk, self.ttk
        self.titre(cadre, "Journal", "Ce qu'affichait la fenêtre noire. Le bouton Copier prend tout, pour me l'envoyer.")
        bloc = ttk.Frame(cadre)
        bloc.pack(fill="both", expand=True)
        zone = tk.Text(bloc, bg="#141414", fg="#e6e6e6", insertbackground="#e6e6e6", relief="flat",
                       font=("Cascadia Mono", 9), wrap="word", padx=8, pady=6)
        defil = ttk.Scrollbar(bloc, orient="vertical", command=zone.yview)
        zone.configure(yscrollcommand=defil.set)
        zone.pack(side="left", fill="both", expand=True)
        defil.pack(side="right", fill="y")
        vues = [None]

        def rafraichir():
            if not zone.winfo_exists() or self.page != "journal":
                return
            lignes = list(self.d.JOURNAL)
            # Le journal garde 500 lignes au plus : une fois plein, seule la dernière ligne change.
            if (len(lignes), lignes[-1:]) != vues[0]:
                bas = zone.yview()[1] > 0.98
                zone.configure(state="normal")
                zone.delete("1.0", "end")
                zone.insert("end", "\n".join(lignes))
                zone.configure(state="disabled")
                if bas:
                    zone.see("end")
                vues[0] = (len(lignes), lignes[-1:])
            zone.after(1000, rafraichir)
        rafraichir()
        zone.see("end")

        def copier():
            self.top.clipboard_clear()
            self.top.clipboard_append("\n".join(self.d.JOURNAL))
        r = self.rangee(cadre)
        ttk.Button(r, text="Copier tout", command=copier).pack(side="left")
