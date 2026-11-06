"""Djåse : dictée vocale 100 % locale, la voix reste sur le PC.
(Née le 21/10/2026 à Liège, chez Manuel Warland.)

Ctrl+Win maintenu : dicter, relâcher pour coller le texte au curseur.
Ctrl+Win appui bref : mains libres ; Ctrl+Win de nouveau pour finir, Échap pour annuler.
Ctrl+Win puis Maj : recoller la dernière dictée. Ctrl+Win+Alt : mode commande.
Une autre touche pendant le maintien (Ctrl+Win+flèche…) annule : c'était un raccourci Windows.

Chaîne : Parakeet TDT v3 et Canary 1B v2 (onnx-asr) transcrivent pendant que vous parlez, plusieurs
« oreilles » départagent les dictées courtes, des règles convertissent la ponctuation dictée, les commandes et le
dictionnaire, Qwen3 4B (llama-server de llama.cpp) nettoie le texte, Grammalecte corrige les accords.
Icône près de l'horloge et fenêtre Djåse : interface.py. Données : chemins.py. Modèles : telechargement.py.
Journal dans %LOCALAPPDATA%\\Djåse\\journal.txt (et dans la console avec « Lancer dictee.cmd »).

Test sans micro ni clavier : python dictee.py --test fichier.wav
"""

import atexit
import ctypes
import json
import math
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np

ICI = Path(__file__).parent
from chemins import DONNEES, MODELES, PROGRAMME, preparer  # noqa: E402
REGLAGES = {
    "entree": "auto",       # 1, 2 ou "auto" (la voie la plus forte de la carte son)
    "nettoyage": True,      # False : règles seules, sans Qwen (plus rapide)
    "moteur": "parakeet",   # "parakeet" (~0,3 s par phrase) ou "whisper" (~5 s, comprend le dictionnaire)
    "fils": 8,              # cœurs pour Whisper et Qwen
    "historique": True,     # garde brut + nettoyé dans historique.jsonl (local)
    "audio": True,          # garde aussi l'audio de chaque dictée dans audio/ (jours d'essai)
    "audio_jours": 30,      # WAV plus vieux supprimés au lancement (0 : jamais)
    "historique_jours": 365,  # dictées plus vieilles retirées de historique.jsonl au lancement (0 : jamais)
    "ecoute_apres": 0.3,    # secondes d'écoute après le relâchement (dernier mot pas coupé)
    "grammaire": True,      # corrections sûres de Grammalecte (accords, conjugaisons), en local
    "double_espace": False, # deux espaces après « . ? ! » (habitude de machine à écrire ; True chez Manu, dans son reglages.json)
    "canary_court": True,   # dictée de moins de 2 s → Canary 1B v2, français imposé (+1 Go de mémoire)
    "oreilles": True,       # dictée courte sans commande : Canary et Parakeet notent les commandes sur le son
    "canary_long": True,    # dictée longue (morceau de 2 s et plus) : Canary plutôt que Parakeet (banc du 24/10)
    "troisieme_oreille": True,  # dictée courte : le modèle NVIDIA français en 3e avis (+150 Mo de mémoire)
    "son_messages": True,   # un son Windows à chaque message de la pastille (ding / avertissement)
    "verifier_maj": True,   # une fois par jour, demande à GitHub le numéro de la dernière version (maj.py) ; aucune dictée envoyée
    "style": "formel",      # formel, decontracte, tres_decontracte, enthousiaste
    "style_partout": False, # True : le même style partout, sans regarder l'application
    "styles_applis": {},    # {"whatsapp": "decontracte"} : nom du programme ou mot du titre de la fenêtre
}
fichier_reglages = DONNEES / "reglages.json"
# lu au chargement, AVANT que main() ne déplace les données : au tout premier lancement, encore dans le dossier du code
_reglages_lus = fichier_reglages if fichier_reglages.exists() else ICI / "reglages.json"
if _reglages_lus.exists():
    REGLAGES.update(json.loads(_reglages_lus.read_text(encoding="utf-8")))

RATE = 16000
BLOCK = 1600             # 100 ms
AVANCE = 4               # 400 ms gardées avant l'appui, pour ne pas couper la première syllabe
PARAKEET = REGLAGES["moteur"] != "whisper"
NOM_MOTEUR = "Parakeet" if PARAKEET else "Whisper"
PAUSE_COUPURE = 0.7      # une pause de 0,7 s coupe un morceau...
# ...s'il dure au moins MORCEAU_MIN. Whisper coûte ~5 s par appel quelle que soit la longueur
# (fenêtre fixe de 30 s) : on découpe peu. Parakeet coûte presque rien mais dérive vers
# l'anglais sur les longs passages (90 s d'un bloc) : on découpe souvent, MORCEAU_MAX au plus.
MORCEAU_MIN = 3.0 if PARAKEET else 20.0
MORCEAU_MAX = 25.0
APPUI_BREF = 0.35        # appui plus court : mains libres
SEUIL_MIN = 0.008
PORT_LLM = 8790
MODELE_LLM = MODELES / "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"

BELOW_NORMAL = 0x4000
NO_WINDOW = 0x08000000
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), BELOW_NORMAL)


if sys.stdout is None:  # lancé par pythonw.exe (sans fenêtre noire) : pas de console
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
JOURNAL = deque(maxlen=500)  # dernières lignes, pour l'onglet Journal de la fenêtre Djåse


def journal(texte):
    ligne = f"[{datetime.now():%H:%M:%S}] {texte}"
    JOURNAL.append(ligne)
    try:
        print(ligne, flush=True)
    except Exception:
        pass
    try:
        fichier = DONNEES / "journal.txt"
        if fichier.exists() and fichier.stat().st_size > 1_000_000:
            fichier.replace(DONNEES / "journal.ancien.txt")
        with open(fichier, "a", encoding="utf-8") as f:
            f.write(ligne + "\n")
    except OSError:
        pass


class _LimitesBase(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class _Limites(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _LimitesBase), ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


_job = None


def lier_a_l_appli(proc):
    """Job Windows « tuer à la fermeture » : le serveur meurt avec l'appli, même fermée par la croix."""
    global _job
    try:
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        if _job is None:
            _job = kernel32.CreateJobObjectW(None, None)
            infos = _Limites()
            infos.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            kernel32.SetInformationJobObject(_job, 9, ctypes.byref(infos), ctypes.sizeof(infos))
        if not kernel32.AssignProcessToJobObject(_job, int(proc._handle)):
            journal("Avertissement : serveur Qwen pas lié à l'appli (il pourrait survivre à la fermeture).")
    except Exception as e:
        journal(f"Avertissement : liaison du serveur Qwen impossible ({e}).")


# ---- Dictionnaire et règles ---------------------------------------------------

class Dictionnaire:
    """dictionnaire.json et textes_rapides.json, relus dès qu'ils changent : on peut les éditer pendant que
    l'appli tourne. Les textes rapides sont à part : ils contiennent des données perso (adresse mail…)
    qui ne doivent pas partir avec le logiciel s'il est publié (demande de Manu)."""

    def __init__(self):
        self.fichier = DONNEES / "dictionnaire.json"
        self.fichier_textes = DONNEES / "textes_rapides.json"
        self.date = self.date_textes = None
        self.mots, self.remplacements, self.motifs, self.textes, self.par_appli = [], {}, [], {}, {}

    def actualiser(self):
        try:
            # absent : pas encore en place (premier lancement, main() le copie dans DONNEES) ; relu à la dictée suivante,
            # sans ligne au journal (elle créait un journal neuf avant le déplacement de l'ancien, relecture du 05/11)
            date = self.fichier.stat().st_mtime if self.fichier.exists() else None
            if date is not None and date != self.date:
                donnees = json.loads(self.fichier.read_text(encoding="utf-8"))
                self.mots = donnees.get("mots", [])
                self.remplacements = donnees.get("remplacements", {})
                self.motifs = [(re.compile(m, re.I), r) for m, r in donnees.get("remplacements_motifs", {}).items()
                               if not m.startswith("_")]  # "_note" : commentaire
                # {"fusion360.exe": {"mots": [...], "remplacements": {...}}} : seulement dans cette appli
                self.par_appli = {a: v for a, v in donnees.get("par_appli", {}).items() if isinstance(v, dict)}
                self.date = date
        except (OSError, ValueError, re.error) as e:  # re.error : motif mal écrit (relecture Codex)
            journal(f"dictionnaire.json illisible, ancien gardé : {e}")
        try:
            date = self.fichier_textes.stat().st_mtime if self.fichier_textes.exists() else 0
            if date != self.date_textes:
                donnees = json.loads(self.fichier_textes.read_text(encoding="utf-8")) if date else {}
                self.textes = {d: t for d, t in donnees.items() if not d.startswith("_")}
                self.date_textes = date
        except (OSError, ValueError) as e:
            journal(f"textes_rapides.json illisible, ancien gardé : {e}")
        return self


dico = Dictionnaire().actualiser()

OUVRE = r"ouvr\w*(?:\s+(?:la|les|le|une|des|un)\b|\s+l['’])?\s*"
FERME = r"(?:ferm\w*|fin)(?:\s+(?:de|du|des)\b|\s+d['’])?(?:\s+(?:la|les|le)\b|\s*l['’])?\s*"
# Signes de paire : marques provisoires (\x02…\x0b), espacées correctement par normaliser().
PAIRES = [
    (r"guillemets? droits?", "\x02", "\x03"),
    (r"guillemets?(?: français)?", "\x04", "\x05"),
    (r"parenth[eè]ses?", "\x06", "\x07"),
    (r"crochets?", "\x08", "\x12"),
    (r"accolades?", "\x0e", "\x0f"),
    (r"chevrons?", "\x10", "\x11"),
]
SIGNES_PAIRES = {"\x02": ' "', "\x03": '" ', "\x04": " « ", "\x05": " » ", "\x06": " (", "\x07": ") ",
                 "\x08": " [", "\x12": "] ", "\x0e": " {", "\x0f": "} ", "\x10": " <", "\x11": "> "}

COMMANDES = [
    (r"points? d['’]interrogation", " ? "),
    (r"points? d['’]exclamation", " ! "),
    (r"trois petits points|points? de suspension", " ... "),
    (r"points?[- ]virgules?", " ; "),
    (r"doubles?[- ]points?", " : "),  # « double point » : le deux-points en Belgique
]
for _motif, _ouvre, _ferme in PAIRES:
    COMMANDES += [(OUVRE + _motif, _ouvre), (FERME + _motif, _ferme)]


def accorder_guillemets(texte):
    """« fermez les guillemets » ferme le type ouvert : droits si on a ouvert des guillemets droits."""
    pile, sortie = [], []
    for c in texte:
        if c in ("\x02", "\x04"):
            pile.append(c)
        elif c in ("\x03", "\x05"):
            if c == "\x05" and pile and pile[-1] == "\x02":
                c = "\x03"
            if pile:
                pile.pop()
        sortie.append(c)
    return "".join(sortie)


# Raccourcis clavier dictés : « contrôle plus win plus maj » → « Ctrl+Win+Maj ».
# Seulement une suite de noms de touches reliés par « plus », qui commence par une touche de
# modification : « un contrôle plus strict » ou « le mage plus fort » restent intacts.
TOUCHES_MODIF = {"ctrl": "Ctrl", "contrôle": "Ctrl", "controle": "Ctrl", "control": "Ctrl",
                 "win": "Win", "windows": "Win", "maj": "Maj", "mage": "Maj", "majuscule": "Maj",
                 "shift": "Maj", "alt": "Alt", "alt gr": "AltGr", "altgr": "AltGr"}
TOUCHES_AUTRES = {"entrée": "Entrée", "entree": "Entrée", "échap": "Échap", "echap": "Échap",
                  "échappe": "Échap", "escape": "Échap", "tab": "Tab", "tabulation": "Tab",
                  "suppr": "Suppr", "supprimer": "Suppr", "espace": "Espace", "inser": "Inser",
                  "retour arrière": "Retour arrière", "flèche haut": "Flèche haut",
                  "flèche bas": "Flèche bas", "flèche gauche": "Flèche gauche",
                  "flèche droite": "Flèche droite", "page suivante": "Page suivante",
                  "page précédente": "Page précédente", "home": "Début", "end": "Fin",
                  "impr écran": "Impr écran", "print screen": "Impr écran", "pause": "Pause"}
# F1 à F12, que Parakeet écrive « F5 », « f 5 » ou « F cinq ».
NOMBRES_F = {"10": 10, "11": 11, "12": 12, **{str(n): n for n in range(1, 10)},
             "un": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7,
             "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12}
_F = r"(?:f|effe)\s*(" + "|".join(sorted(NOMBRES_F, key=len, reverse=True)) + r")"
TOUCHE_F_SEULE = re.compile(rf"(?<!\w)(?:{_F}(?<=\d)|(?<=touche ){_F})(?!\w)", re.I)
_MODIF = "|".join(sorted(map(re.escape, TOUCHES_MODIF), key=len, reverse=True)).replace(r"\ ", r"\s+")
_TOUCHE = "|".join(sorted(map(re.escape, {**TOUCHES_MODIF, **TOUCHES_AUTRES}), key=len, reverse=True)) \
    .replace(r"\ ", r"\s+") + r"|(?:f|effe)\s*(?:" + "|".join(NOMBRES_F) + r")|[a-z0-9]"
_PLUS = r"\s*(?:\bplus\b|\+)\s*"
RACCOURCI = re.compile(rf"(?<!\w)(?:{_MODIF})(?:{_PLUS}(?:{_TOUCHE}))+(?!\w)", re.I)


def _nom_touche(mot):
    mot = re.sub(r"\s+", " ", mot.lower())
    f = re.fullmatch(_F, mot)
    if f:
        return f"F{NOMBRES_F[f.group(1)]}"
    return TOUCHES_MODIF.get(mot) or TOUCHES_AUTRES.get(mot) or mot.upper()


def raccourcis_clavier(texte):
    texte = TOUCHE_F_SEULE.sub(lambda m: f"F{NOMBRES_F[(m.group(1) or m.group(2)).lower()]}", texte)
    return RACCOURCI.sub(lambda m: "+".join(_nom_touche(t) for t in
                                            re.split(_PLUS, m.group(0), flags=re.I)), texte)


# Nombres : Parakeet écrit « 8 » ou « huit » pour le même son selon le découpage (rejeu du 23/10).
# Règle française (ma proposition, acceptée par Manu le 23/10, heures en « 17h30 ») : chiffres pour
# les heures, les listes (1, 2, 3), les unités (5 euros, 10 %), les dates (23 octobre) et à partir
# de 17 ; lettres pour les petits nombres dans une phrase (un jour ou deux, les deux) et les rangs.
UNITES_NB = {"zéro": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6,
             "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13,
             "quatorze": 14, "quinze": 15, "seize": 16}
# Manu compte à la belge (septante, nonante) ; octante/huitante (Suisse) au passage.
DIZAINES_NB = {"vingt": 20, "vingts": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60,
               "septante": 70, "octante": 80, "huitante": 80, "nonante": 90}
_MOT_NB = "|".join(sorted([*UNITES_NB, *DIZAINES_NB, "cents", "cent", "mille"], key=len, reverse=True))
_NB = rf"(?:{_MOT_NB})(?:(?:[\s-]+et[\s-]+|[\s-]+)(?:{_MOT_NB}))*(?![\w-])"
_JETON_NB = rf"(?:\d+|(?:{_MOT_NB})(?:-(?:et-)?(?:{_MOT_NB}))*)(?![\w-])"
_MOIS = r"janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre"
_MESURES = (r"euros?|€|centimes?|dollars?|\$|%|pour\s?cent|kilos?|kg|grammes?|tonnes?|mètres?|centimètres?|cm"
            r"|millimètres?|mm|kilomètres?|km|litres?|millilitres?|ml|degrés?|°|volts?|ampères?|milliampères?"
            r"|mA|mAh|watts?|ohms?|hertz|Hz|kilohertz|mégahertz|MHz|octets?|ko|mégaoctets?|Mo|gigaoctets?|Go"
            r"|téraoctets?|To|pixels?|px|bits?|millisecondes?|ms")
HEURE = re.compile(rf"(?<![\w-])({_NB}|\d{{1,2}})\s+heures?(?:\s+(et demie|et quart|{_NB}|\d{{1,2}}(?![\w-])))?",
                   re.I)
DATE = re.compile(rf"(?<![\w-])(premier|{_NB})(?=\s+(?:{_MOIS})\b)", re.I)
MESURE = re.compile(rf"(?<![\w-])({_NB})(?=\s*(?:{_MESURES})(?!\w))", re.I)
LISTE = re.compile(rf"(?<![\w-]){_JETON_NB}(?:(?:\s*,\s*|\s+){_JETON_NB}){{2,}}", re.I)
GRAND = re.compile(rf"(?<![\w-]){_NB}", re.I)
DECIMAL = re.compile(rf"(?<![\w-])({_NB}|\d+)\s+virgule\s+({_NB}|\d+)(?![\w-])", re.I)


def valeur_nb(mots_nb):
    """« quatre-vingt-dix-sept » → 97, « 12 » → 12 ; None si ce n'est pas UN nombre (« sept huit »)."""
    if mots_nb.isdigit():
        return int(mots_nb)
    mots = [m for m in re.split(r"[\s-]+", mots_nb.lower()) if m]
    total = cur = 0
    for i, m in enumerate(mots):
        bas = cur % 100
        if m == "et":
            if not (bas in (20, 30, 40, 50, 60, 70, 80, 90) and mots[i + 1:i + 2] in (["un"], ["une"], ["onze"])):
                return None
        elif m in UNITES_NB:
            v = UNITES_NB[m]
            if (v == 0 and len(mots) > 1) or not (
                    bas == 0 or (bas in (20, 30, 40, 50, 60, 70, 80, 90) and 0 < v < 10)
                    or (bas in (60, 80) and v >= 10) or (bas in (10, 70, 90) and 7 <= v <= 9)):
                return None
            cur += v
        elif m in DIZAINES_NB:
            if m.startswith("vingt") and bas == 4:
                cur += 76  # quatre-vingt
            elif bas == 0:
                cur += DIZAINES_NB[m]
            else:
                return None
        elif m in ("cent", "cents"):
            if cur > 19 or cur == 10:  # « douze cents » (1200) se dit ; « vingt cents » non (relecture GLM)
                return None
            cur = (cur or 1) * 100
        elif m == "mille":
            if total:
                return None
            total, cur = (cur or 1) * 1000, 0
    return total + cur


def en_chiffres(v):
    return f"{v:,}".replace(",", " ") if v >= 10000 else str(v)


def _heure(m):
    h, mn = valeur_nb(m.group(1)), m.group(2)
    if mn:
        mn = {"et demie": 30, "et quart": 15}.get(mn.lower()) or valeur_nb(mn)
        if mn is None or not 0 < mn < 60:
            return m.group(0)
    if h is None or h > 24:
        return m.group(0)
    # Sans minutes, « deux heures » est souvent une durée : heure seulement après 12 ou après « à »…
    if mn is None and h < 13 and not re.search(r"(?:\b(?:à|a|vers|dès|avant|après|est|était|entre)|jusqu['’]à)\s+$",
                                               m.string[:m.start()], re.I):
        return m.group(0)
    return f"{h}h{mn:02d}" if mn else f"{h}h"


def _liste(m):
    if "," not in m.group(0) and valeur_nb(m.group(0)) is not None:
        return m.group(0)  # « deux cent cinquante » est un seul nombre, pas une liste
    valeurs = [valeur_nb(j.group(0)) for j in re.finditer(_JETON_NB, m.group(0), re.I)]
    if None in valeurs:
        return m.group(0)
    liste = re.sub(_JETON_NB, lambda j: en_chiffres(valeur_nb(j.group(0))), m.group(0), flags=re.I)
    # Parakeet oublie parfois une virgule (« 6, 7 8 ») : liste à virgules → virgules partout.
    return re.sub(r"(?<=\d)\s+(?=\d)", ", ", liste) if "," in liste else liste


def _nombre_si(m, condition):
    v = valeur_nb(m.group(0))
    return en_chiffres(v) if v is not None and condition(v, m.group(0).lower()) else m.group(0)


def _decimal(m):
    a, b = valeur_nb(m.group(1)), valeur_nb(m.group(2))
    return f"{a},{b}" if a is not None and b is not None else m.group(0)


def nombres(texte):
    texte = DECIMAL.sub(_decimal, texte)
    # « trois volts trois » (façon de dire 3,3 V) → « 3,3 volts ».
    texte = re.sub(rf"(?<![\w-])({_NB}|\d+)\s+(volts?)\s+({_NB}|\d)(?![\w-])",
                   lambda m: f"{valeur_nb(m.group(1))},{valeur_nb(m.group(3))} {m.group(2)}"
                   if valeur_nb(m.group(1)) is not None and (valeur_nb(m.group(3)) or 10) < 10 else m.group(0),
                   texte, flags=re.I)
    texte = HEURE.sub(_heure, texte)
    texte = DATE.sub(lambda m: "1er" if m.group(0).lower() == "premier"
                     else _nombre_si(m, lambda v, t: 1 <= v <= 31), texte)
    texte = MESURE.sub(lambda m: _nombre_si(m, lambda v, t: t not in ("un", "une")), texte)
    texte = re.sub(r"(\d)\s*pour\s?cent\b", r"\1 %", texte, flags=re.I)
    texte = LISTE.sub(_liste, texte)
    return GRAND.sub(lambda m: _nombre_si(m, lambda v, t: v >= 17 and t not in ("cent", "cents", "mille")), texte)


def regles(texte):
    """Ce qui est mécanique : ponctuation dictée sans ambiguïté et orthographe du dictionnaire."""
    # Whisper écrit parfois lui-même « … » pour « ouvrez les guillemets » : on les reprend
    # en marques pour que « fermez » s'accorde, et « « droits » redevient des guillemets droits.
    texte = re.sub(r"«\s*droits?\b,?\s*", "\x02", texte, flags=re.I)
    # Parakeet coupe « guillemets, droits » par une virgule : « droit » finirait dans la citation.
    texte = re.sub(r"\b(guillemets?)\s*,\s*droits?\b", r"\1 droits", texte, flags=re.I)
    texte = texte.replace("«", "\x04").replace("»", "\x05")
    for motif, signe in COMMANDES:
        # Les signes de paire ne mangent pas les points voisins : fin de phrase ou « ... » à garder.
        bord = r"[\s,]*" if signe in SIGNES_PAIRES else r"[\s,.]*"
        texte = re.sub(bord + r"\b(?:" + motif + r")\b" + bord, signe, texte, flags=re.I)
    # Commandes ambiguës dans une phrase, mais pas quand Whisper les isole entre deux pauses.
    texte = re.sub(r"[.,]\s*\b(?:deux|2) points\b\s*[.,]", " : ", texte, flags=re.I)
    # « virgule » en fin de dictée : une commande (essai de Manu du 23/10, restait en toutes lettres).
    texte = re.sub(r"[\s,]*\bvirgules?\s*[.!]?\s*$", ",", texte, flags=re.I)
    # Listes et paragraphes à la voix (idée de Codex, 23/10). Chaque élément est annoncé : rien à retenir
    # entre deux dictées. Pas « puce » (électronique) ni « nouvel élément » (trop courant dans une phrase).
    texte = re.sub(r"[\s,]*\bnouveau paragraphe\b[\s,.:;]*", "\n\n", texte, flags=re.I)
    # Le signe AVANT « début de liste » finit la phrase d'avant : gardé ; avant « élément suivant » ou
    # « fin de liste », c'est la fin d'un élément : retiré. « l'élément suivant » (dans une phrase) : intact.
    # Variantes entendues le 23/10 (20h19) : « éléments suivants », « fin de list », « fad List ».
    texte = re.sub(r"[\s,]*\bdébut de (?:la )?liste?\b[\s,.:;]*", "\n- ", texte, flags=re.I)
    texte = re.sub(r"[\s,.:;]*(?<!['’])\béléments? suivants?\b[\s,.:;]*", "\n- ", texte, flags=re.I)
    texte = re.sub(r"[\s,.:;]*\b(?:fin|fad|fa) de (?:la )?liste?\b[\s,.:;]*|[\s,.:;]*\bfad list\b[\s,.:;]*", "\n", texte,
                   flags=re.I)
    texte = re.sub(r"(^|[.,!?:;])\s*\b(?:retour à la ligne|(?:à|a) la (?:ligne|l[ií]nea)|nouvelle ligne)\b\s*[.,]?",
                   lambda m: (m.group(1) if m.group(1) not in ",;:" else ".") + "\n" if m.group(1) else "\n",
                   texte, flags=re.I)
    texte = accorder_guillemets(texte)
    for marque, signe in SIGNES_PAIRES.items():
        texte = texte.replace(marque, signe)
    texte = re.sub(r'([(\[{<]|(?:^|\s)")\s+', r"\1", texte)   # rien après un signe ouvrant
    texte = re.sub(r'\s+([)\]}>])', r"\1", texte)              # rien avant un signe fermant
    texte = re.sub(r'(\S)\s+"(?=\s|$|[.,;:?!])', r'\1"', texte)  # guillemet droit fermant collé
    texte = raccourcis_clavier(texte)
    # Parakeet colle parfois l'heure : « treizeur vingt six » → « treize heures vingt six » (puis 13h26).
    # Le « e » final fond dans « heures » : « treiz-eur » → racine + e.
    texte = re.sub(r"\b(un|deux|trois|quatr|cinq|six|sept|huit|neuf|dix|onz|douz|treiz|quatorz|quinz|seiz|vingt)"
                   r"e?-?eure?s?\b",
                   lambda m: m.group(1) + ("e" if m.group(1)[-1] in "nrz" else "")
                   + (" heure" if m.group(1).lower() == "un" else " heures"), texte, flags=re.I)
    texte = nombres(texte)
    for motif, juste in dico.motifs:
        texte = motif.sub(juste, texte)
    for faux, juste in dico.remplacements.items():
        texte = re.sub(r"(?<!\w)" + re.escape(faux) + r"(?!\w)", juste, texte, flags=re.I)
    for mot in dico.mots:
        texte = re.sub(r"(?<!\w)" + re.escape(mot) + r"(?!\w)", mot, texte, flags=re.I)
    # Élisions oubliées (« que on » → « qu'on ») ; pas devant h ni y (h aspiré, « que y a-t-il »).
    texte = re.sub(r"\b([QqJjDdNnMmTtSs]|[Qq]u)(?:e)\s+(?=[aeiouéèêàâîôûAEIOUÉÈÊÀÂÎÔÛ])",
                   lambda m: m.group(1) + "'", texte)
    texte = re.sub(r"\b([Ss])i\s+(ils?)\b", r"\1'\2", texte)
    if texte.strip(" ") == "\n":
        return "\n"  # « à la ligne » dicté seul : un saut de ligne, pas du vide
    return normaliser(texte)


def normaliser(t):
    """Typographie française et ponctuation de Manu (« ... » espacés)."""
    t = t.replace("…", "...")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s+,", ",", t)
    t = re.sub(r",(\s*,)+", ",", t)
    t = re.sub(r",\s*([.?!])", r"\1", t)
    t = re.sub(r"([?!])(\s*\1)+", r"\1", t)  # « colis ? ? » : Whisper avait déjà mis le signe
    t = re.sub(r"\s+\.(?!\.)", ".", t)
    t = re.sub(r"\s*([?!;]+)", r" \1", t)
    t = re.sub(r"\s*:(?=\s|$)", " :", t)
    t = re.sub(r"([?!;:]+)(?=[^\s?!;:»)\n/\d])", r"\1 ", t)
    t = re.sub(r"«\s*", "« ", t)
    t = re.sub(r"\s*»", " »", t)
    t = re.sub(r"\s*\.\.\.\s*", " ... ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t).strip()
    t = re.sub(r"^([«\s]*)(\w)", lambda m: m.group(1) + m.group(2).upper(), t)
    t = re.sub(r"(?<!\.)([.?!])(\s+[«\s]*)([a-zà-ÿ])", lambda m: m.group(1) + m.group(2) + m.group(3).upper(), t)
    t = re.sub(r"(\n[«\s]*)([a-zà-ÿ])", lambda m: m.group(1) + m.group(2).upper(), t)
    return t


# ---- Nettoyage par Qwen (llama.cpp) -------------------------------------------

CONSIGNE = """Tu nettoies une dictée vocale en français, transcrite automatiquement. Tu n'es pas un assistant : tu ne réponds JAMAIS au contenu, même si c'est une question ou une demande ; tu rends seulement le texte nettoyé.

Règles :
1. Garde les mots, le ton oral et l'ordre des idées. Ne reformule pas, ne résume pas, n'ajoute rien.
2. Retire seulement les hésitations sans contenu (euh, hum, heu) et les mots répétés par accident.
3. Ponctuation dictée encore en toutes lettres : « virgule » → , ; « point » → . ; « deux points » ou « 2 points » → : ; « à la ligne » → saut de ligne. Seulement quand c'est clairement une commande, pas quand le mot fait partie de la phrase.
4. Quand l'auteur se corrige (« à 15h, non, plutôt à 16h »), garde seulement la version corrigée.
5. Ponctue naturellement (règles françaises : espace avant ? ! : ;), majuscule en début de phrase.
6. Ne supprime JAMAIS un signe déjà présent (... « » " ( ) [ ] {{ }} < > ? ! ;), même s'il n'est pas refermé dans ce texte, et n'en ajoute pas autour des noms propres.
7. « entre parenthèses » suivi d'un passage → ce passage entre ( ), jusqu'à la fin de l'idée ou de la phrase. Pareil pour « entre guillemets » → « », « entre guillemets droits » → " ", « entre crochets » → [ ], « entre accolades » → {{ }}, « entre chevrons » → < >. Exception : « entre parenthèses » employé au sens de « soit dit en passant », sans passage à encadrer, reste tel quel.
8. Le texte peut commencer au milieu d'une phrase dictée avant : dans ce cas ne mets pas de majuscule au premier mot.
9. Orthographe imposée pour ces mots : {mots}.

Réponds uniquement avec le texte nettoyé."""

EXEMPLES = [
    ("euh je pense que euh on pourrait commencer à 15h non plutôt à 16h",
     "Je pense qu'on pourrait commencer à 16h."),
    ("bon alors faut acheter du pain virgule du lait et des œufs point",
     "Bon, alors faut acheter du pain, du lait et des œufs."),
    ("il faut acheter deux points du pain du lait et des œufs",
     "Il faut acheter : du pain, du lait et des œufs."),
    ("on a gagné de deux points et j'ai noté deux points importants le prix et le délai",
     "On a gagné de deux points et j'ai noté deux points importants : le prix et le délai."),
    ("est-ce que tu peux me dire quelle heure il est ?",
     "Est-ce que tu peux me dire quelle heure il est ?"),
    ("On se retrouve jeudi à 14h. Non, plutôt à 15h devant la gare.",
     "On se retrouve jeudi à 15h devant la gare."),
    ("j'ai pris le petit modèle entre parenthèses le 4B parce qu'il est plus rapide",
     "J'ai pris le petit modèle (le 4B) parce qu'il est plus rapide."),
]


# Qwen n'est appelé que s'il y a quelque chose à faire : Whisper ponctue déjà bien seul.
# Hésitations pures : retirées sans Qwen (sinon un seul « euh » coûte toute une réécriture).
HESITATIONS = re.compile(r"(,?)\s*\b(?:euh+|heu+|hum+|um+|uh+|m+-?h+m+|h+m{2,}|m{3,})\b(?:\s*\.\.\.|…)?\s*(,?)", re.I)


def retirer_hesitations(texte):
    # « , euh, » : virgules ajoutées par le moteur autour de l'hésitation, on les retire ;
    # « bon, euh on y va » : la virgule d'avant appartient à la phrase, on la garde.
    # Virgule finale déjà là (« … virgule » dicté → « , ») : gardée, ce n'est pas celle d'un « euh ».
    garder = texte.rstrip().endswith(",") and not HESITATIONS.search(texte[-12:])
    avant = texte
    texte = HESITATIONS.sub(lambda m: ", " if m.group(1) and not m.group(2) else " ", texte)
    texte = re.sub(r"[ \t]{2,}", " ", texte).strip(" ,")  # pas \s : « nouveau paragraphe » (\n\n) survivait pas (23/10)
    if texte != avant.strip(" ,") and not re.search(r"\w", texte):
        return ""  # « Mm-hmm. » seul : pas de point orphelin collé (un « point » dicté, lui, ne passe pas ici)
    return texte + "," if garder and texte else texte


A_NETTOYER = re.compile(
    r"\b(?:bah|ben)\b"                                                  # hésitations (ambiguës)
    r"|\bnon\b[^.?!]{0,30}\bplutôt\b|\bou plutôt\b|\bje veux dire\b"    # auto-corrections
    r"|\bpardon\b|\benfin non\b|\bje me corrige\b|\bje recommence\b"
    r"|\bvirgule\b|\b(?:deux|2) points\b|\bà la ligne\b|\bnouvelle ligne\b|\bpoint\b(?! de vue)"  # ponctuation dite
    r"|\bentre (?:parenthèses|guillemets|crochets|accolades|chevrons)\b"
    r"|\b(\w{2,})\s+\1\b",                                              # mot répété
    re.I)


RETIRABLES = set("euh euhh heu hum um bah ben non plutôt enfin pardon je veux dire point points virgule "
                 "deux à la ligne nouvelle entre parenthèses parenthèse guillemets guillemet crochets "
                 "crochet accolades accolade chevrons chevron droits".split())


def mots(texte):
    return re.findall(r"\w+", texte.lower())


def mots_perdus(avant, apres):
    """Mots présents avant le nettoyage et absents après (en comptant les doublons)."""
    reste = {}
    for m in mots(apres):
        reste[m] = reste.get(m, 0) + 1
    perdus = []
    for m in mots(avant):
        if reste.get(m, 0):
            reste[m] -= 1
        elif m not in RETIRABLES:
            perdus.append(m)
    return perdus


class Nettoyeur:
    def __init__(self):
        self.pret = threading.Event()
        self.proc = None

    def demarrer(self):
        self.pret.clear()  # aussi au retour d'une pause (menu de l'icône)
        if not REGLAGES["nettoyage"] or not MODELE_LLM.exists():
            journal("Nettoyage par Qwen désactivé : règles seules.")
            return
        # Serveurs restés orphelins (fenêtre fermée par la croix avant cette version).
        serveur = str(PROGRAMME / "llama" / "llama-server.exe")
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Get-Process llama-server -ErrorAction SilentlyContinue | "
                        f"Where-Object Path -eq '{serveur}' | Stop-Process -Force"],
                       creationflags=NO_WINDOW, capture_output=True)
        self.proc = subprocess.Popen(
            [str(PROGRAMME / "llama" / "llama-server.exe"), "-m", str(MODELE_LLM), "--port", str(PORT_LLM),
             "-t", str(REGLAGES["fils"]), "-c", "4096", "--no-webui", "--host", "127.0.0.1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=BELOW_NORMAL | NO_WINDOW)
        lier_a_l_appli(self.proc)
        atexit.register(self.arreter)
        threading.Thread(target=self._attendre, daemon=True).start()

    def _attendre(self):
        t = time.time()
        while self.proc.poll() is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT_LLM}/health", timeout=2) as r:
                    if r.status == 200:
                        self._appeler("Bonjour, euh, test.")  # charge la consigne dans le cache
                        journal(f"Qwen prêt ({time.time() - t:.0f} s).")
                        self.pret.set()
                        return
            except Exception:
                pass
            time.sleep(0.5)
        journal("llama-server s'est arrêté : règles seules.")

    def _appeler(self, texte):
        messages = [{"role": "system", "content": CONSIGNE.format(mots=", ".join(dico.mots))}]
        for brut, propre in EXEMPLES:
            messages += [{"role": "user", "content": brut}, {"role": "assistant", "content": propre}]
        messages.append({"role": "user", "content": texte})
        corps = json.dumps({"messages": messages, "temperature": 0, "max_tokens": 1024,
                            "cache_prompt": True}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{PORT_LLM}/v1/chat/completions", corps,
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)["choices"][0]["message"]["content"].strip()

    def nettoyer(self, texte):
        texte = retirer_hesitations(texte)
        if not self.pret.is_set() or len(texte) < 3 or not A_NETTOYER.search(texte):
            return texte
        try:
            propre = self._appeler(texte)
        except Exception as e:
            journal(f"Nettoyage en échec ({e}) : texte gardé tel quel.")
            return texte
        # Garde-fou : un modèle qui répond au lieu de nettoyer change fortement la longueur.
        if len(texte) > 20 and not 0.5 <= len(propre) / len(texte) <= 1.4:
            journal("Nettoyage écarté (longueur suspecte) : texte gardé tel quel.")
            return texte
        # Garde-fou : Qwen ne doit retirer que des hésitations, de la ponctuation dite ou
        # la partie corrigée (« 15h, non, plutôt »), jamais une phrase entière.
        perdus = mots_perdus(texte, propre)
        if len(perdus) > max(3, len(mots(texte)) // 8):
            journal(f"Nettoyage écarté (mots supprimés : {' '.join(perdus)}) : texte de Whisper gardé.")
            return texte
        return propre

    def arreter(self):
        self.pret.clear()  # en pause : la fenêtre ne doit plus dire « Qwen prêt »
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


# ---- Grammaire (Grammalecte, local) -------------------------------------------

# Seulement ce qui se trompe à l'oral sans se voir à l'oreille : accords, conjugaisons,
# participes, impératif, homophones. Typographie, majuscules, virgules : coupées.
OPTIONS_GRAMMAIRE = {k: False for k in ("typo", "apos", "eepi", "esp", "nbsp", "maj", "minis", "num",
                                         "virg", "unit", "nf", "chim", "date", "pleo", "latex", "md")}


ORTHO = [None]  # dictionnaire de Grammalecte, une fois chargé : dit si un mot est un nom propre
ELISION = re.compile(r"(?:J|L|C|D|N|S|M|T|Qu|Jusqu|Lorsqu|Puisqu|Aujourd)['’]")


def minuscule_de_suite(texte):
    """Dictée qui prolonge une dictée sans point final (« Je suis en retard » puis « Mais je suis bien
    arrivé ») : pas de majuscule au premier mot (demande de Manu du 24/10). Gardée pour un nom propre
    (Paul, Liège), un mot du vocabulaire (MemCore, Codex), un sigle ou un mot inconnu ; sans
    Grammalecte, rien ne change."""
    m = re.match(r"([«  ]*)([A-ZÀ-Ý][\w'’-]*)", texte)
    ortho = ORTHO[0]
    if not m or ortho is None:
        return texte
    mot = m.group(2)
    if not ELISION.match(mot):
        if (mot.isupper() and len(mot) > 1) or any(mot == d.split()[0] for d in dico.mots):
            return texte
        if not ortho.isValid(mot.lower()) or any(":M" in x for x in ortho.getMorph(mot)):
            return texte
    return texte[:m.start(2)] + mot[0].lower() + texte[m.start(2) + 1:]


class Grammaire:
    def __init__(self):
        self.moteur = None
        self.fini = threading.Event()  # chargement terminé (réussi ou non) : pour le message « Prêt »
        if REGLAGES["grammaire"]:
            threading.Thread(target=self._charger, daemon=True).start()
        else:
            self.fini.set()

    def _charger(self):
        try:
            import grammalecte
            correcteur = grammalecte.GrammarChecker("fr")
            moteur = correcteur.getGCEngine()
            moteur.setOptions(OPTIONS_GRAMMAIRE)
            moteur.parse("Les tableau que j'ai fais.", "FR")  # premier appel lent : à vide
            self.moteur = moteur
            ORTHO[0] = correcteur.getSpellChecker()
            journal("Grammalecte prêt.")
        except Exception as e:
            journal(f"Grammalecte indisponible ({e}) : pas de correction de grammaire.")
        finally:
            self.fini.set()

    def corriger(self, texte):
        """Applique les corrections sûres ; renvoie (texte, [(avant, après), ...]).
        Deux passages : un accord en entraîne souvent un autre (« Elles est partie » →
        « Elles sont partie » → « Elles sont parties »)."""
        # Ligne par ligne : Grammalecte lisait par-dessus les sauts de ligne (« - les\nMerci » → « Mercis »,
        # liste dictée du 23/10).
        lignes, faites = [], []
        for ligne in texte.split("\n"):
            for _ in range(2):
                ligne, nouvelles = self._passe(ligne)
                if not nouvelles:
                    break
                faites += nouvelles
            lignes.append(ligne)
        return "\n".join(lignes), faites

    def _passe(self, texte):
        if self.moteur is None or not texte.strip():
            return texte, []
        try:
            erreurs = self.moteur.parse(texte, "FR")
        except Exception as e:
            journal(f"Grammalecte en échec ({e}).")
            return texte, []
        # « Ils mange » : deux corrections qui se contredisent (« Il » ou « mangent ») ;
        # on garde la première action de la règle (a1), celle sur le verbe.
        def action(e):
            m = re.search(r"_a(\d+)_\d+$", e["sRuleId"])
            return int(m.group(1)) if m else 0
        par_regle = {}
        for e in sorted(erreurs, key=action):
            par_regle.setdefault(re.sub(r"_a\d+_\d+$", "", e["sRuleId"]), e)
        retenues, fin = [], -1
        for e in sorted(par_regle.values(), key=lambda e: e["nStart"]):
            sugg = list(dict.fromkeys(s.replace("’", "'") for s in e["aSuggestions"]))
            mot = texte[e["nStart"]:e["nEnd"]]
            # Plusieurs suggestions : on ne prend la première que si elle ajoute une simple
            # marque d'accord (« contente » → « contentes » plutôt que « contentées »).
            accord = sugg and sugg[0].lower() != mot.lower() and sugg[0].lower().startswith(mot.lower()) \
                and sugg[0][len(mot):].lower() in ("e", "s", "x", "es")
            if not (len(sugg) == 1 or accord) or e["nStart"] < fin \
                    or any(m.lower() in mot.lower() for m in dico.mots):
                continue
            retenues.append((e["nStart"], e["nEnd"], sugg[0]))
            fin = e["nEnd"]
        faites = []
        for debut, fin, juste in reversed(retenues):
            avant = texte[debut:fin]
            if juste[:1].isupper() != avant[:1].isupper():
                juste = (juste[:1].upper() if avant[:1].isupper() else juste[:1].lower()) + juste[1:]
            texte = texte[:debut] + juste + texte[fin:]
            faites.append((avant, juste))
        return texte, faites[::-1]


# ---- Style et textes rapides (sans Qwen : de simples règles, instantanées) ------

STYLES = {"formel": "Formel", "decontracte": "Décontracté", "tres_decontracte": "Très décontracté",
          "enthousiaste": "Enthousiaste"}


def appli_devant(hwnd=None):
    """(programme, titre) de la fenêtre qui va recevoir le collage (par défaut celle au premier plan)."""
    hwnd = hwnd or user32.GetForegroundWindow()
    titre = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, titre, 512)
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    nom = ""
    kernel32.OpenProcess.restype = ctypes.c_void_p
    poignee = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if poignee:
        chemin, taille = ctypes.create_unicode_buffer(520), ctypes.c_ulong(520)
        if kernel32.QueryFullProcessImageNameW(ctypes.c_void_p(poignee), 0, chemin, ctypes.byref(taille)):
            nom = Path(chemin.value).name.lower()
        kernel32.CloseHandle(ctypes.c_void_p(poignee))
    return nom, titre.value


def vocabulaire_appli(texte, programme, titre):
    """Mots et remplacements propres à une application (idée de Codex, 23/10) : les noms de Fusion 360
    ne débordent pas dans WhatsApp. Même règle que les styles : nom du programme ou mot du titre."""
    for cle, vocab in dico.par_appli.items():
        if not cle.strip() or not (cle.lower() in programme or cle.lower() in titre.lower()):
            continue
        for faux, juste in vocab.get("remplacements", {}).items():
            texte = re.sub(r"(?<!\w)" + re.escape(faux) + r"(?!\w)", lambda m: juste, texte, flags=re.I)
        for mot in vocab.get("mots", []):
            texte = re.sub(r"(?<!\w)" + re.escape(mot) + r"(?!\w)", lambda m: mot, texte, flags=re.I)
    return texte


def style_pour(programme, titre):
    """Exceptions par application (nom du programme ou mot du titre : « whatsapp » marche
    pour l'appli comme pour l'onglet de Chrome), sinon le style par défaut."""
    if not REGLAGES["style_partout"]:
        for cle, style in REGLAGES["styles_applis"].items():
            if cle.strip() and (cle.lower() in programme or cle.lower() in titre.lower()):
                return style
    return REGLAGES["style"]


TOUCHE_ENTREE = "\x00entree"  # marque : appuyer sur Entrée au lieu de coller

# Une dictée qui n'est QUE de la ponctuation dite : c'est une commande, même pour « point »
# (ambigu dans une phrase, « je te demande un point », mais pas seul).
PONCTUATION_SEULE = {"point": ".", "point final": ".", "virgule": ",", "deux points": ":", "double point": ":",
                     "double points": ":", "point virgule": ";", "point d'interrogation": "?",
                     "point d'exclamation": "!", "point à la ligne": ".\n", "point a la ligne": ".\n",
                     "trois petits points": "...", "points de suspension": "...", "espace": " ",
                     # Ici aussi pour que l'arbitrage garde Canary quand lui seul l'entend (13h27 :
                     # Parakeet vide puis « Yeah », Canary « à la ligne »).
                     "à la ligne": "\n", "a la ligne": "\n", "nouvelle ligne": "\n",
                     # « point » / « virgule » dits seuls et brefs, mal entendus par Parakeet (relevés
                     # sur les audios du 23/10 ; aucun traitement du son ne règle ça de façon fiable).
                     # Seulement quand c'est TOUTE la dictée. Manu ne dicte jamais en anglais.
                     **{m: "." for m in ("poi", "pue", "pues", "pua", "puhe", "пуэ", "poe", "poé", "poids", "poet",
                                         "points", "why", "where", "wheel", "way", "waar", "what")},
                     "virgil": ",", "virgul": ",", "virguel": ",", "virgules": ",",
                     "espase": " ", "l'espace": " ", "alla ligne": "\n",  # variantes (rejeu du 23/10)
                     # Listes et paragraphes dits seuls (voir regles()).
                     "nouveau paragraphe": "\n\n", "fin de liste": "\n", "fin de la liste": "\n",
                     "début de liste": "\n- ", "début de la liste": "\n- ", "élément suivant": "\n- ",
                     # Touche Entrée (envoyer un message) : « entrée » / « envoie » dits seuls, et ce
                     # que Parakeet en fait (essai de Manu du 23/10 : Entrez, Enter, Envoyé, En).
                     **{m: TOUCHE_ENTREE for m in ("entrée", "entree", "entrez", "enter", "envoie", "envoyer",
                                                   "envoyé", "envoi", "en")}}
# Ponctuation dite en fin de dictée : le style ne la retire pas (seulement le point ajouté par Djåse).
PONCTUATION_DITE_FIN = re.compile(r"(?:\bpoints?(?:\s+(?:final|virgule|d['’]\w+))?|\bvirgule|\b(?:à|a) la ligne"
                                  r"|\bnouvelle ligne)\W*$", re.I)
# Deux espaces après une fin de phrase (pas après « ... », pas dans « gmail.com » : il faut une espace).
DOUBLE_ESPACE = re.compile(r"(?<!\.)([.?!]) (?=[^\s.])")
FIN_DE_PHRASE = re.compile(r"(?<!\.)[.?!]$")
LIGNE_DITE_FIN = re.compile(r"(?:\b(?:à|a) la ligne|\bnouvelle ligne|\bfin de (?:la )?liste)\W*$", re.I)
PARAGRAPHE_DIT_FIN = re.compile(r"\bnouveau paragraphe\W*$", re.I)
# Dictée qui commence par une liste ou un paragraphe : normaliser() a retiré les sauts de ligne du début.
DEBUT_DIT = re.compile(r"^\W*(?:(nouveau paragraphe)|début de (?:la )?liste)\b", re.I)
DANS_LISTE = re.compile(r"(?:^|\n)- [^\n]*$")  # dernière ligne = élément de liste : pas de point final


def est_commande(texte):
    """Dictée reconnue comme commande (signe dit seul, Entrée, corrige, efface) : sert à choisir
    entre Parakeet et Canary sur une dictée courte."""
    if not texte:
        return False
    regle = regles(texte)
    return ponctuation_seule(texte) is not None or bool(commande_efface(regle) or commande_corrige(regle)
                                                        or commande_forme(regle))


def ponctuation_seule(brut):
    cle = re.sub(r"[^\w'’ -]", "", brut).replace("’", "'").replace("-", " ")
    return PONCTUATION_SEULE.get(re.sub(r"\s+", " ", cle).strip().lower())


def appliquer_style(texte, style, fin_dite=False):
    """fin_dite : la ponctuation finale a été dictée (« … point »), le style la garde."""
    if style not in STYLES or style == "formel" or not texte.strip() or not re.search(r"\w", texte):
        return texte
    point_final = texte.endswith(".") and not texte.endswith("...") and not fin_dite
    if style == "enthousiaste":
        return texte[:-1] + " !" if point_final else texte  # espace avant « ! », comme avant « ? »
    if point_final:
        texte = texte[:-1]
    if style == "tres_decontracte":
        texte = re.sub(r"\s*,(?=\s|$)", "", texte)

        def minuscule(m):  # sauf les mots du dictionnaire (MemCore, GitHub…) et les sigles
            mot = m.group(2)
            if (mot.isupper() and len(mot) > 1) or any(mot == d.split()[0] for d in dico.mots):
                return m.group(0)
            return m.group(1) + mot[0].lower() + mot[1:]
        texte = re.sub(r"(^|[.?!…]\s+|\n)([A-ZÀ-Ý][\w'’-]*)", minuscule, texte)
    return texte


TOLERANCE_TEXTES = 0.75  # « S remon Git Up » 0,77 passe ; « Ceci est un test » 0,29 non
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre")


def variables(contenu):
    """Champs d'un texte rapide, calculés au moment de la dictée (idée de Codex, 23/10) :
    {date} 23/10/2026, {date_longue} vendredi 23 octobre 2026, {heure} 18h24."""
    if "{" not in contenu:
        return contenu
    m = datetime.now()
    return (contenu.replace("{date_longue}", f"{JOURS[m.weekday()]} {m.day} {MOIS[m.month - 1]} {m.year}")
            .replace("{date}", f"{m:%d/%m/%Y}").replace("{heure}", f"{m.hour}h{m.minute:02d}"))


def textes_rapides(texte):
    """« insère mon GitHub » → le lien, dit n'importe où ; le déclencheur est celui que Manu a écrit.
    Dicté seul : le texte sans le point ni la majuscule ajoutés autour."""
    for declencheur, contenu in dico.textes.items():
        mots_d = re.findall(r"\w+", declencheur)
        if not mots_d:
            continue
        motif = r"(?<!\w)" + r"[\s,.'’-]*".join(map(re.escape, mots_d)) + r"(?!\w)"
        if re.fullmatch(r"\W*" + motif + r"\W*", texte, re.I):
            return variables(contenu)
        texte = re.sub(motif, lambda m: variables(contenu), texte, flags=re.I)
    # Dictée faite SEULEMENT d'un déclencheur mal entendu (« S remon Git Up », « Smile et chapeau ») :
    # tolérance, choix de Manu ; jamais au milieu d'une phrase (risque de coller l'adresse mail n'importe où).
    serre = re.sub(r"\W", "", texte.lower())
    if serre and len(texte.split()) <= 6:
        import difflib

        def note_de(declencheur):
            mots_d = re.findall(r"\w+", declencheur.lower())
            note = difflib.SequenceMatcher(None, serre, "".join(mots_d)).ratio()
            # Le 1er mot saute souvent (« Insère » mangé : « More Facebook », « Mon GitHub ») : on compare
            # aussi sans lui, 0,10 plus sévère (« C'est mon GitHub » 0,82 ne passe pas, « More Facebook » 0,87 oui).
            if len(mots_d) >= 3:
                note = max(note, difflib.SequenceMatcher(None, serre, "".join(mots_d[1:])).ratio() - 0.10)
            # Déclencheur court (« insère la date ») : facile à confondre (« Insère l'adresse » 0,77) ;
            # 0,10 plus sévère (« Insère la data » 0,92 passe toujours).
            return note - (0.10 if len("".join(mots_d)) <= 12 else 0)
        note, declencheur = max(((note_de(d), d) for d in dico.textes), default=(0, None))
        if note >= TOLERANCE_TEXTES:
            journal(f"  Texte rapide reconnu approximativement : « {texte} » → « {declencheur} » ({note:.2f})")
            return variables(dico.textes[declencheur])
    return texte


# ---- Transcription ------------------------------------------------------------

HALLUCINATIONS = re.compile(r"sous-titr|amara\.org|merci d'avoir regardé|abonnez-vous", re.I)


def paire_ouverte(texte):
    """Parenthèse, crochet, accolade ou guillemet encore ouvert à la fin de la dictée : la
    suite viendra dans la dictée suivante (« ferme la parenthèse »), qui portera le point."""
    return any(texte.count(a) > texte.count(b) for a, b in ("()", "[]", "{}", "«»")) \
        or texte.count('"') % 2 == 1


def assembler(morceaux):
    """Recolle les morceaux ; un morceau qui commence par une majuscule ouvre une nouvelle
    phrase : si le précédent ne finissait pas par un signe, on ajoute le point manquant."""
    texte = ""
    for m in (m for m in morceaux if m):
        if texte and texte[-1] not in ".?!:;…»)\n" and re.match(r"[A-ZÀ-Ý][a-zà-ÿ'’]", m) \
                and m.split()[0].strip(",.") not in dico.mots:
            texte += "."
        texte = (texte + " " + m) if texte else m
    return texte


# Arbitrage à l'oreille (oreilles.py) : phrase notée → texte rendu (que est_commande reconnaît).
# « jazz » : c'est ainsi que les deux moteurs écrivent « Djåse ». Mode commande : verbes nus.
SIGNES_DITS = ("point", "virgule", "deux points", "point virgule", "point d'interrogation", "point d'exclamation",
               "à la ligne", "point à la ligne", "nouvelle ligne", "trois petits points", "espace", "entrée", "envoie")
EFFACE_DITS = ("efface", "efface tout", "supprime", "retire", "annule", "enlève")
# Mise en forme dite seule (« en gras » entendu « Угра » / « Oh grave », 23/10 20h18) : dans les deux modes.
FORMES_DITES = ("en gras", "en italique", "en barré", "tout en gras", "tout en italique", "tout en barré", "en citation")
OREILLES_NORMAL = {**{s: s[0].upper() + s[1:] + "." for s in SIGNES_DITS},
                   **{"jazz " + e: "Djåse, " + e + "." for e in EFFACE_DITS},
                   **{f: f[0].upper() + f[1:] + "." for f in FORMES_DITES}}
OREILLES_COMMANDE = {e: e[0].upper() + e[1:] + "." for e in (*EFFACE_DITS, *FORMES_DITES)}
# Mesures du 23/10 : vraies commandes −0,15 à −2,8 par jeton dans chaque oreille, vraies phrases ≤ −4,3.
SEUIL_OREILLES, MARGE_OREILLES = -3.0, 0.5
MARGE_COMMANDE = -8.0  # mode commande : la commande peut perdre de peu contre une phrase (voir _oreilles)
# Dictée longue : Canary en second avis seulement si Parakeet entend un de ces mots (voir _longue).
MOTS_DE_COMMANDE = re.compile(r"\b(?:liste?|list|éléments?|gras|itali(?:que|c)s?|barr[ée]e?s?|citation|paragraphe)\b",
                              re.I)


class Transcripteur:
    def __init__(self, nettoyeur, sortie):
        self.nettoyeur = nettoyeur
        self.grammaire = Grammaire()
        self.sortie = sortie          # fonction(texte, session) appelée à la fin d'une dictée
        self.file = queue.Queue()
        self.pret = threading.Event()
        self.modele = None
        self.canary = None  # dictées courtes (voir _charger)
        self.canary_fini = threading.Event()
        self.oreilles = None  # arbitrage à l'oreille des dictées courtes (oreilles.py)
        self.francais = None  # 3e avis sur les dictées courtes : NVIDIA français (voir _charger)
        self.erreur = None    # moteur principal impossible à charger : dit à la pastille et à l'icône  # tentative de chargement de Canary terminée
        threading.Thread(target=self._charger, daemon=True).start()
        threading.Thread(target=self._boucle, daemon=True).start()

    def _charger(self):
        t = time.time()
        try:
            if PARAKEET:
                import onnx_asr
                dossier = MODELES / "parakeet-tdt-0.6b-v3"  # installé au premier lancement (telechargement.py)
                if dossier.is_dir():
                    self.modele = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", dossier, quantization="int8")
                else:  # développement : cache Hugging Face, comme avant
                    self.modele = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", quantization="int8")
            else:
                from faster_whisper import WhisperModel
                self.modele = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8",
                                           cpu_threads=REGLAGES["fils"])
        except Exception as e:
            # Sans ça, « chargement » sans fin et sans un mot (relecture Codex du 23/10).
            self.erreur = f"{NOM_MOTEUR} impossible à charger ({e})"
            journal(f"ERREUR : {self.erreur}. Djåse ne peut pas dicter : relancez-le ; si ça se répète, "
                    "joignez ce journal à un signalement de problème.")
            self.pret.set()
            self.canary_fini.set()
            return
        journal(f"{NOM_MOTEUR} prêt ({time.time() - t:.0f} s).")
        self.pret.set()
        # Canary après Parakeet : Djåse est prêt aussi vite qu'avant, Canary prend le relais ensuite.
        # Mot isolé bref (« virgule », « espace ») : Parakeet rend du vide ou part en anglais, Canary
        # (français imposé) le reconnaît ; sur les phrases, Parakeet reste meilleur (essai du 23/10).
        dossier = MODELES / "canary-1b-v2"
        if PARAKEET and REGLAGES["canary_court"] and dossier.exists():
            try:
                import onnx_asr
                t = time.time()
                self.canary = onnx_asr.load_model("nemo-canary-1b-v2", dossier, quantization="int8")
                journal(f"Canary prêt pour les dictées courtes ({time.time() - t:.0f} s).")
                # Toujours prêtes (aucune mémoire en plus) : le réglage est lu à chaque dictée.
                from oreilles import DeuxOreilles
                self.oreilles = DeuxOreilles(self.canary, self.modele)
            except Exception as e:
                journal(f"Canary indisponible ({e}) : Parakeet pour tout.")
        # Banc du 24/10 (40 phrases lues par Manu) : mauvais sur le long, mais le seul à entendre « en gras »,
        # « du lait », « point » dits seuls. Il ne décide jamais seul : sa transcription passe aux oreilles.
        dossier = MODELES / "nvidia-fr-conformer-transducer-large-onnx"
        if self.oreilles is not None and REGLAGES["troisieme_oreille"] and dossier.exists():
            try:
                import onnx_asr
                t = time.time()
                self.francais = onnx_asr.load_model("nemo-conformer-rnnt", dossier, quantization="int8")
                journal(f"Modèle français prêt pour les dictées courtes ({time.time() - t:.0f} s).")
            except Exception as e:
                journal(f"Modèle français indisponible ({e}) : Parakeet et Canary seuls.")
        self.canary_fini.set()

    def _transcrire(self, session, audio):
        texte = self._parakeet(audio) if PARAKEET else self._whisper(session, audio)
        courte = session.fin is not None and session.fin - session.debut < 2.0 and not session.brut
        appel = "Djåse, " if session.commande else ""  # mode commande : « retire » seul est une commande
        if PARAKEET and self.canary is not None and self.oreilles is not None and REGLAGES["oreilles"] \
                and not courte and MOTS_DE_COMMANDE.search(texte):
            return self._longue(session, audio, texte)
        if est_commande(appel + texte):
            return texte
        if PARAKEET and self.canary is not None and not courte and REGLAGES["canary_long"]:
            return self._canary_long(session, audio, texte)
        if not (PARAKEET and self.canary is not None and courte):
            return texte
        # Dictée courte : Canary (français imposé) en second avis. Rejeu du 23/10 : il reconnaît
        # « virgule », « espace », « Djåse efface » que Parakeet rate, mais casse « Entrez ». On garde
        # le moteur qui a reconnu une commande ; sinon Parakeet, sauf vide ou autre alphabet (russe).
        try:
            canary = re.sub(r"\s*<unk>\s*", " ", self.canary.recognize(audio, sample_rate=RATE, language="fr")).strip()
        except Exception as e:  # Canary en panne : le texte de Parakeet, plutôt que perdre la dictée (relecture Codex)
            journal(f"  Canary en échec ({e}) : Parakeet gardé.")
            return texte
        if not texte and canary.strip(" .!?").lower() in ("ouais", "quoi", "puh"):
            # « point » dit seul : Parakeet n'entend rien, Canary entend « Ouais » (23/10). Un vrai
            # « Ouais », Parakeet l'entend, lui : on n'arrive pas ici.
            session.moteur = "Canary"
            return "Point."
        if not texte and canary and not est_commande(appel + canary):
            # Parakeet n'entend rien, même avec du silence autour : Canary invente sur un bruit (24/10 :
            # « Je suis en train de vous montrer… » sur deux petits bruits, « Non » sur un raclement de gorge).
            # Rejeu de 249 dictées courtes : hors « Ouais » ci-dessus, il n'a jamais eu raison dans ce cas.
            journal(f"  Parakeet n'entend rien, Canary seul : « {canary} » ignoré (bruit).")
            return ""
        choix = texte
        if canary and (est_commande(appel + canary) or not texte or re.search(r"[^\x00-ɏ\s\W]", texte)):
            session.moteur = "Canary"
            choix = canary
        if self.oreilles is None or not REGLAGES["oreilles"] or not choix or est_commande(appel + choix):
            return choix
        francais = self._francais(audio)
        choix = self._departager(session, audio, choix, texte, canary, francais)
        # Plus de 4 mots (aucune commande n'est si longue) ou commande reconnue après départage : c'est fini.
        if len(choix.split()) > 4 or est_commande(appel + choix):
            return choix
        return self._oreilles(session, audio, choix, (texte, canary, francais), appel)

    def _francais(self, audio):
        """3e avis sur une dictée courte (banc du 24/10) : le modèle NVIDIA français, qui écrit sans majuscule
        ni ponctuation ; mis à la forme de Parakeet (« du lait » → « Du lait. »). Vide s'il n'est pas là."""
        if self.francais is None or not REGLAGES["troisieme_oreille"]:
            return ""
        try:
            texte = re.sub(r"\s*<unk>\s*", " ", self.francais.recognize(audio, sample_rate=RATE)).strip()
        except Exception as e:
            journal(f"  Modèle français en échec ({e}) : ignoré.")
            return ""
        return (texte[:1].upper() + texte[1:] + ".") if texte else ""

    def _canary_long(self, session, audio, texte):
        """Dictée longue (morceau de 2 s et plus) : Canary plutôt que Parakeet (banc du 24/10, 40 phrases lues
        par Manu : 8,9 % de mots faux contre 11,8 % ; ~2 s pour 15 s de son, pendant la dictée sauf le dernier
        morceau). Parakeet reste s'il n'entend rien (Canary inventerait sur du bruit), si Canary rend un
        autre alphabet, ou beaucoup moins de mots (passage avalé)."""
        if not texte:
            return texte
        t = time.time()
        try:
            canary = re.sub(r"\s*<unk>\s*", " ", self.canary.recognize(audio, sample_rate=RATE, language="fr")).strip()
        except Exception as e:
            journal(f"  Canary en échec ({e}) : Parakeet gardé.")
            return texte
        if not canary or re.search(r"[^\x00-ɏ\s\W]", canary) or len(canary.split()) < len(texte.split()) / 2:
            journal(f"  Dictée longue : Parakeet gardé, Canary avait « {canary} »")
            return texte
        # Canary oublie parfois le point final sur un morceau bref (« ce truc », rejeu du 24/10) : celui de
        # Parakeet, sinon la dictée suivante continuerait en minuscule (minuscule_de_suite).
        if texte[-1] in ".?!…" and canary[-1] not in ".?!…":
            canary = canary.rstrip(",;: ") + texte[-1]
        journal(f"  Dictée longue : Canary ({time.time() - t:.1f} s) ; Parakeet avait « {texte} »")
        session.moteur = "Canary"
        return canary

    def _longue(self, session, audio, texte):
        """Dictée longue où Parakeet entend un mot de commande (liste, gras…) : Canary aussi, puis les oreilles
        départagent (choix de Manu du 23/10, 20h44). Parakeet perdait « pain » dans une liste et écrivait
        « Fadurat » pour « fin du gras » ; Canary les entendait. Les autres dictées longues ne changent pas."""
        t = time.time()
        try:
            canary = re.sub(r"\s*<unk>\s*", " ", self.canary.recognize(audio, sample_rate=RATE, language="fr")).strip()
        except Exception as e:
            journal(f"  Canary en échec ({e}) : Parakeet gardé.")
            return texte
        journal(f"  Dictée avec commande : Canary aussi ({time.time() - t:.1f} s)")
        return self._departager(session, audio, texte, texte, canary)

    def _departager(self, session, audio, choix, parakeet, canary, francais=""):
        """Parakeet et Canary pas d'accord : les deux oreilles choisissent la transcription qui colle le mieux
        au son (choix de Manu du 23/10, 19h47). Rejeu de 200 dictées courtes : ~11 corrections (« Ouais »
        devenu « Well, I think it's a very good thing », « vraiment au bout » pour « beau »…), 2 pertes.
        Le modèle français (dictées courtes, 24/10) est un 3e candidat. Jamais un autre alphabet (« Бу ») :
        il n'est pas candidat, la règle d'avant garde la main."""
        from oreilles import Oreille
        candidats = {}  # texte → moteur ; une seule fois chaque forme (Parakeet d'abord, sa mise en forme)
        for moteur, x in (("Parakeet", parakeet), ("Canary", canary), ("Français", francais)):
            if x and not re.search(r"[^\x00-ɏ\s\W]", x) \
                    and Oreille.forme(x) not in {Oreille.forme(c) for c in candidats}:
                candidats[x] = moteur
        if len(candidats) < 2 or (not francais and (not parakeet or not canary
                                                    or re.search(r"[^\x00-ɏ\s\W]", parakeet + canary))):
            return choix
        t = time.time()
        try:
            notes = self.oreilles.departager(audio, list(candidats))
        except Exception as e:
            journal(f"  Départage en panne ({e}) : « {choix} » gardé.")
            return choix
        if len(notes) < 2:
            return choix
        meilleur = max(notes, key=notes.get)
        detail = ", ".join(f"{candidats[x]} {n:.1f}" for x, n in notes.items()) + f", {time.time() - t:.2f} s"
        if meilleur != choix:
            journal(f"  Départage : « {choix} » → « {meilleur} » ({detail})")
            session.moteur = candidats[meilleur]
        else:
            journal(f"  Départage : « {choix} » gardé ({detail})")
        return meilleur

    def _oreilles(self, session, audio, choix, entendus, appel):
        """Aucune commande reconnue : Canary et Parakeet notent sur le son chaque commande possible contre
        ce qu'ils ont transcrit (« Yeah » / « Aline » → à la ligne, « I've got it » → efface). Ajoute une
        commande ratée, n'en retire jamais ; en cas de souci, le choix habituel reste."""
        t = time.time()
        commandes = OREILLES_COMMANDE if session.commande else OREILLES_NORMAL
        try:
            # Pas de liste de mots courts (oui, yeah…) en concurrents : un mot bref a toujours une meilleure note
            # qu'une commande longue (« Jess, Fastu » perdait contre « yeah », 23/10 19h21). Un vrai « Yeah »
            # reste protégé : Parakeet l'a transcrit, il est dans « entendus ».
            # Une transcription dans un autre alphabet n'est pas un concurrent (« Джаз Эф Асту » battait
            # « jazz efface tout », 23/10 20h20).
            libres = [x for x in entendus if x and not re.search(r"[^\x00-ɏ\s\W]", x)]
            # Mode commande (Ctrl+Win+Alt) : rien d'autre qu'une commande n'est jamais collé, donc une commande qui
            # colle au son (seuil par jeton) suffit, même si une phrase colle un peu mieux (« en gras » / « Oh grave »,
            # « efface » / « Et face », 23/10 20h18). En mode normal, la marge reste : un vrai mot reste un mot.
            marge = MARGE_COMMANDE if session.commande else MARGE_OREILLES
            rendu, detail = self.oreilles.ecouter(audio, commandes, [*libres, *dico.textes], SEUIL_OREILLES, marge)
        except Exception as e:
            journal(f"  Oreilles en panne ({e}) : « {choix} » gardé.")
            return choix
        if rendu and est_commande(appel + rendu):
            journal(f"  Oreilles : « {choix} » → « {rendu} » ({detail}, {time.time() - t:.2f} s)")
            session.moteur = "Oreilles"
            return rendu
        journal(f"  Oreilles : « {choix} » gardé (meilleure commande {detail}, {time.time() - t:.2f} s)")
        return choix

    def _parakeet(self, audio):
        # Pas d'amorce possible : le vocabulaire passe par les remplacements du dictionnaire.
        texte = re.sub(r"\s*<unk>\s*", " ", self.modele.recognize(audio, sample_rate=RATE)).strip()
        if not texte and len(audio):
            # Mot court dit seul (« point », « virgule ») : Parakeet rend parfois du vide (23/10).
            # Nouvel essai avec 0,5 s de silence autour ; le journal dit si ça a aidé.
            silence = np.zeros(RATE // 2, np.float32)
            texte = re.sub(r"\s*<unk>\s*", " ", self.modele.recognize(
                np.concatenate([silence, audio, silence]), sample_rate=RATE)).strip()
            journal(f"  Rien reconnu ; 2e essai avec du silence autour : « {texte} »")
        return texte

    def _whisper(self, session, audio):
        mots = ", ".join(dico.mots)
        amorce = f"Vocabulaire : {mots}." + (" " + session.brut[-200:] if session.brut else "")
        segments, _ = self.modele.transcribe(audio, language="fr", beam_size=1, vad_filter=True,
                                             initial_prompt=amorce, hotwords=mots)
        return " ".join(s.text.strip() for s in segments if not HALLUCINATIONS.search(s.text)).strip()

    def envoyer(self, session, blocs, final):
        audio = np.concatenate(blocs) if blocs else np.zeros((0, 2), np.float32)
        self.file.put((session, audio, final))

    def _voie(self, audio):
        if audio.ndim == 1:
            return audio
        if REGLAGES["entree"] in (1, 2) and audio.shape[1] >= REGLAGES["entree"]:
            return audio[:, REGLAGES["entree"] - 1]
        return audio[:, int(np.argmax(np.sqrt(np.mean(audio**2, axis=0))))]

    def _boucle(self):
        while True:
            session, audio, final = self.file.get()
            try:
                if not session.annulee and (len(audio) or final):
                    self.pret.wait()
                    if self.erreur:
                        if final:
                            signaler("Moteur de dictée en panne · voir le Journal", alerte=True)
                            self.sortie("", session)
                        continue
                    self._morceau(session, self._voie(audio).astype(np.float32) if len(audio) else None, final)
                if final and not session.annulee:
                    texte = assembler(session.morceaux)
                    texte = "\n" if texte.strip(" ") == "\n" else normaliser(texte)
                    if paire_ouverte(texte):
                        if texte.endswith(".") and not texte.endswith("..."):
                            texte = texte[:-1]
                    elif texte.strip() and texte[-1] not in ".,?!:;»)]}>\"\n" and not DANS_LISTE.search(texte):
                        # (« , » dicté : la suite viendra ; élément de liste : pas de point)
                        texte += "."
                    texte, session.grammaire = self.grammaire.corriger(texte)
                    # Après Grammalecte : il ne touche ni au lien ni à l'adresse d'un texte rapide.
                    session.appli = appli_devant(session.fenetre)
                    session.style = style_pour(*session.appli)
                    seule = ponctuation_seule(session.brut)
                    if seule is not None:
                        texte = seule
                    else:
                        texte = vocabulaire_appli(texte, *session.appli)
                        texte = mise_en_forme(texte, *session.appli)
                        texte = appliquer_style(texte, session.style, bool(PONCTUATION_DITE_FIN.search(session.brut)))
                        if REGLAGES["double_espace"]:  # avant les textes rapides : leur contenu reste tel quel
                            texte = DOUBLE_ESPACE.sub(r"\1  ", texte)
                        texte = textes_rapides(texte)
                        # « …, à la ligne » en fin de dictée : normaliser() a retiré le saut de ligne final.
                        if PARAGRAPHE_DIT_FIN.search(session.brut) and not texte.endswith("\n\n"):
                            texte = texte.rstrip("\n") + "\n\n"
                        elif LIGNE_DITE_FIN.search(session.brut) and not texte.endswith("\n"):
                            texte += "\n"
                        debut = DEBUT_DIT.match(session.brut)
                        if debut and not texte.startswith("\n"):
                            texte = ("\n\n" if debut.group(1) else "\n") + texte
                    self.sortie(texte, session)
            except Exception as e:
                journal(f"Erreur de transcription : {e}")
                if final:
                    self.sortie("", session)

    def _morceau(self, session, audio, final):
        dico.actualiser()
        brut = ""
        if audio is not None:
            t = time.time()
            brut = self._transcrire(session, audio)
            session.t_whisper += time.time() - t
            session.brut = (session.brut + " " + brut).strip()
        if session.reste and re.match(r"[A-ZÀ-Ý][a-zà-ÿ]", brut) and brut.split()[0].strip(",.") not in dico.mots:
            brut = brut[0].lower() + brut[1:]  # suite de la phrase gardée en attente : pas de majuscule
        texte = (session.reste + " " + brut).strip()
        session.reste = ""
        if not final:
            # Une pause n'est pas forcément une fin de phrase : Whisper met un point à chaque
            # morceau. On ne nettoie que les phrases finies ; la dernière attend le morceau suivant.
            if texte.endswith(".") and not texte.endswith("..."):
                texte = texte[:-1]
            fins = list(re.finditer(r"(?:[.?!…]|\.\.\.)(?=\s)", texte))
            coupe = fins[-1].end() if fins else 0
            if len(mots(texte[coupe:])) > 30:
                # Parakeet ponctue moins que Whisper : sans fin de phrase, on ne garde pas tout
                # en attente (sinon Qwen traite toute la dictée d'un coup à la fin).
                virgules = [m.end() for m in re.finditer(r",(?=\s)", texte) if m.end() > coupe]
                coupe = virgules[-1] if virgules and len(mots(texte[virgules[-1]:])) < 15 else len(texte)
            texte, session.reste = texte[:coupe].strip(), texte[coupe:].strip()
        if not texte:
            return
        t = time.time()
        propre = regles(self.nettoyeur.nettoyer(regles(texte)))
        session.t_nettoyage += time.time() - t
        session.morceaux.append(propre)
        session.journal.append({"brut": texte, "propre": propre})


# ---- Micro --------------------------------------------------------------------

class Session:
    def __init__(self):
        self.debut = time.time()
        self.fin = None
        self.annulee = False
        self.mains_libres = False
        self.blocs = []
        self.tout = []           # tout l'audio de la dictée, pour audio/ (réécoute des erreurs)
        self.silence = 0.0
        self.parole = 0.0
        self.parole_totale = 0.0
        self.brut = ""
        self.reste = ""          # fin de phrase pas encore terminée, gardée pour le morceau suivant
        self.morceaux = []
        self.journal = []
        self.t_whisper = 0.0
        self.t_nettoyage = 0.0
        self.grammaire = []      # corrections de Grammalecte appliquées : [(avant, après)]
        self.appli = ("", "")    # (programme, titre) de la fenêtre qui reçoit le collage
        self.moteur = NOM_MOTEUR  # « Canary » pour une dictée courte
        self.commande = False     # Ctrl+Win+Alt : la dictée est une commande
        self.style = "formel"
        self.fenetre = None       # fenêtre au premier plan au relâchement : on ne colle que là


def energie(bloc):
    return float(np.sqrt(np.mean(bloc**2, axis=0)).max())


class Micro:
    def __init__(self, transcripteur):
        self.transcripteur = transcripteur
        self.verrou = threading.Lock()
        self.avance = deque(maxlen=AVANCE)
        self.bruit = deque(maxlen=100)
        self.session = None
        self.niveau = 0.0            # lu par la pastille pour animer les barres
        self.file = queue.Queue()

    def seuil(self):
        bruit = float(np.median(self.bruit)) if self.bruit else 0.0
        return min(max(bruit * 3, SEUIL_MIN), 0.02)

    def ouvrir(self):
        import sounddevice as sd
        voies = min(2, sd.query_devices(kind="input")["max_input_channels"])
        journal(f"Micro : {sd.query_devices(kind='input')['name']} ({voies} voie(s)).")
        self.flux = sd.InputStream(samplerate=RATE, channels=voies, dtype="float32", blocksize=BLOCK,
                                   callback=lambda data, *_: self.file.put(data.copy()))
        self.flux.start()
        threading.Thread(target=self._boucle, daemon=True).start()

    def _boucle(self):
        while True:
            self.recevoir(self.file.get())

    def recevoir(self, bloc):
        e = energie(bloc)
        self.niveau = e
        with self.verrou:
            s = self.session
            if s is None:
                self.avance.append(bloc)
                self.bruit.append(e)
                return
            s.blocs.append(bloc)
            s.tout.append(bloc)
            if e > self.seuil():
                s.silence = 0.0
                s.parole += BLOCK / RATE
                s.parole_totale += BLOCK / RATE
            else:
                s.silence += BLOCK / RATE
            duree = len(s.blocs) * BLOCK / RATE
            pause = s.silence >= PAUSE_COUPURE and duree >= MORCEAU_MIN
            trop_long = PARAKEET and duree >= MORCEAU_MAX and s.silence >= 0.2
            if (pause or trop_long or (PARAKEET and duree >= MORCEAU_MAX + 5)) and s.parole >= 0.5:
                # Coupe au milieu du silence, pas à sa fin : le dernier bloc peut déjà contenir
                # le début du mot suivant, et un bout de mot isolé fait inventer des mots à
                # Parakeet (« 8, 8, 8, 1 » le 21/10). La fin du silence ouvre le morceau suivant.
                garder = min(int(round(s.silence * RATE / BLOCK)) // 2, len(s.blocs) - 1)
                envoi, s.blocs = (s.blocs[:-garder], s.blocs[-garder:]) if garder > 0 else (s.blocs, [])
                self.transcripteur.envoyer(s, envoi, final=False)
                s.parole = 0.0

    def demarrer(self):
        with self.verrou:
            if self.session is not None and self.session.fin is not None:
                self._envoyer_fin()  # nouvel appui pendant l'écoute après relâchement
            s = Session()
            s.blocs = list(self.avance)
            s.tout = list(self.avance)
            self.session = s
        return s

    def arreter(self, annuler=False):
        with self.verrou:
            s = self.session
            if s is None or s.fin is not None:
                return s
            s.fin = time.time()
            s.fenetre = user32.GetForegroundWindow()  # celle qui doit recevoir le texte
            if annuler or s.parole_totale < 0.2:
                self.session = None
                s.annulee = True
                return s
        # On relâche souvent les touches pendant le dernier mot (« Ce tru… » rendait du vide
        # le 22/10) : comme Wispr Flow, on écoute encore un instant avant d'envoyer.
        threading.Timer(REGLAGES["ecoute_apres"], self._finir, (s,)).start()
        return s

    def _finir(self, s):
        with self.verrou:
            if self.session is s:
                self._envoyer_fin()

    def _envoyer_fin(self):
        """Appelé sous verrou : envoie le dernier morceau de la session en cours."""
        s, self.session = self.session, None
        blocs = s.blocs if s.parole >= 0.2 else []
        self.transcripteur.envoyer(s, blocs, final=True)


# ---- Collage au curseur -------------------------------------------------------

# pause : menu de l'icône ; pastille : pour signaler() ; tout_pret : tous les modèles chargés
ETAT = {"pause": False, "pastille": None, "tout_pret": False}


def signaler(texte, alerte=False):
    """Court message sur la pastille (en plus du journal, pas à sa place)."""
    if ETAT["pastille"]:
        ETAT["pastille"].message(texte, alerte)


VK_CTRL, VK_LWIN, VK_RWIN, VK_MAJ = 0x11, 0x5B, 0x5C, 0x10
DERNIERE = [""]  # dernière dictée, pour Ctrl+Win puis Maj (recoller)
# Dernier collage, pour « Djåse, corrige X par Y » : on ne l'efface (retours arrière) que si
# rien n'a été tapé ni cliqué depuis, et que la fenêtre n'a pas changé.
# bloc : les dictées collées d'affilée (rien tapé ni cliqué entre), pour « corrige » et « efface » :
# « Le chat est vraiment beau » puis « point » puis « Djåse, efface vraiment » (23/10).
COLLAGE = {"texte": "", "bloc": "", "intact": False, "fenetre": None, "muet": 0.0}


def collage_touche():
    if time.time() > COLLAGE["muet"]:  # pas nos propres Ctrl+V et retours arrière
        COLLAGE["intact"] = False


def surveiller_collage():
    """Un clic ou un changement de fenêtre après le collage : on ne sait plus où est le curseur."""
    while True:
        time.sleep(0.05)
        if COLLAGE["intact"] and (any(touche_tenue(vk) for vk in (0x01, 0x02, 0x04))
                                  or user32.GetForegroundWindow() != COLLAGE["fenetre"]):
            COLLAGE["intact"] = False


def charger_derniere():
    """Au démarrage : la dernière dictée de historique.jsonl, recollable même après relance."""
    try:
        lignes = (DONNEES / "historique.jsonl").read_text(encoding="utf-8").splitlines()
        textes = (json.loads(l).get("texte", "") for l in reversed(lignes[-50:]))
        DERNIERE[0] = next((t for t in textes if t.strip()), "")  # pas une dictée vide
    except Exception:
        pass


def purger_audio():
    """WAV de plus de REGLAGES["audio_jours"] jours supprimés : audio/ grossissait sans fin (~2 Mo par 30 s ;
    relectures Codex et GLM, choix de Manu du 23/10). L'historique texte a sa propre durée (purger_historique)."""
    jours = REGLAGES["audio_jours"]
    if not jours or not (DONNEES / "audio").is_dir():
        return
    limite = time.time() - jours * 86400
    vieux = [f for f in (DONNEES / "audio").glob("*.wav") if f.stat().st_mtime < limite]
    for f in vieux:
        try:
            f.unlink()
        except OSError:
            pass
    if vieux:
        journal(f"Audio : {len(vieux)} enregistrement(s) de plus de {jours} jours supprimé(s).")


def purger_historique():
    """Dictées de plus de REGLAGES["historique_jours"] jours retirées de historique.jsonl (1 an, choix de Manu
    du 23/10 : vie privée). Au lancement, avant le raccourci : aucune dictée ne s'écrit pendant ce temps.
    Fichier temporaire puis remplacement : un arrêt brutal ne laisse jamais un historique à moitié écrit."""
    jours = REGLAGES["historique_jours"]
    fichier = DONNEES / "historique.jsonl"
    if not jours or not fichier.exists():
        return
    from datetime import timedelta
    limite = (datetime.now() - timedelta(days=jours)).isoformat(timespec="seconds")
    try:
        lignes = fichier.read_text(encoding="utf-8").splitlines()

        def recente(ligne):
            try:
                return json.loads(ligne)["date"] >= limite  # dates ISO : l'ordre du texte est celui du temps
            except (ValueError, KeyError, TypeError):
                return True  # ligne illisible : gardée, on ne jette pas ce qu'on ne comprend pas
        non_vides = [l for l in lignes if l.strip()]
        gardees = [l for l in non_vides if recente(l)]
        if len(gardees) == len(non_vides):
            return
        provisoire = fichier.with_suffix(".jsonl.tmp")
        provisoire.write_text("".join(l + "\n" for l in gardees), encoding="utf-8")
        os.replace(provisoire, fichier)
        journal(f"Historique : {len(non_vides) - len(gardees)} dictée(s) de plus de {jours} jours retirée(s).")
    except OSError as e:
        journal(f"Historique pas purgé ({e}).")


def touche_tenue(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def ecran_verrouille():
    """Bureau sécurisé affiché (Win+L, invite d'administrateur) : le bureau des touches n'est pas accessible."""
    h = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
    if not h:
        return True
    user32.CloseDesktop(h)
    return False


def attendre_relachement():
    for _ in range(40):  # Win encore enfoncé transformerait Ctrl+V en Ctrl+Win+V
        if not any(touche_tenue(vk) for vk in (VK_CTRL, VK_LWIN, VK_RWIN, VK_MAJ)):
            break
        time.sleep(0.05)


def coller(texte, avant="", continuer=False):
    """continuer : ce collage prolonge le bloc précédent (dictée à la suite, rien tapé entre).
    « avant » (une espace) est collé devant mais pas compté dans COLLAGE : « corrige » n'efface que le texte."""
    import keyboard
    import pyperclip
    attendre_relachement()
    try:
        ancien = pyperclip.paste()
    except Exception:
        ancien = None
    pyperclip.copy(avant + texte)
    time.sleep(0.05)
    COLLAGE["muet"] = time.time() + 0.5
    keyboard.send("ctrl+v")
    bloc = COLLAGE["bloc"] + avant + texte if continuer and COLLAGE["intact"] else texte
    COLLAGE.update(texte=texte, bloc=bloc, intact=True, fenetre=user32.GetForegroundWindow())
    time.sleep(0.4)
    if ancien is not None:
        try:
            pyperclip.copy(ancien)
        except Exception:
            pass


# ---- Pastille animée ----------------------------------------------------------

class Pastille:
    """Petite pilule sombre en bas au centre : barres qui suivent la voix, vague pendant le calcul.

    Jamais d'activation : la fenêtre ne prend pas le focus (le collage doit aller à l'appli
    en cours) et laisse passer les clics. Cachée = déplacée hors de l'écran.
    """

    L, H, BARRES = 160, 36, 11  # icône de Djåse à gauche des barres (demande de Manu), place du point rouge à droite
    CLE = "#ff00fe"  # couleur rendue transparente (coins arrondis)
    FOND, BORD = "#0f1a35", "#2a3a5e"  # bleu nuit de la charte LumAutoSam

    def __init__(self, micro):
        import tkinter as tk
        import tkinter.font as tkfont
        self.micro = micro
        # Cachée au départ : l'icône près de l'horloge montre le chargement (démarrage avec Windows).
        self.etat = "cachee"
        self.msg = None          # (texte, alerte, fin) : court message à la place des barres
        self.taches = queue.Queue()  # fonctions à exécuter dans le fil de tkinter (menu, fenêtre)
        self.largeur = self.L
        self.hauteurs = [3.0] * self.BARRES
        self.phase = 0.0
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", self.CLE)
        self.root.attributes("-alpha", 0.95)
        self.canevas = tk.Canvas(self.root, width=self.L, height=self.H, bg=self.CLE, highlightthickness=0)
        self.canevas.pack()
        self.police = tkfont.Font(family="Segoe UI", size=10)
        try:  # la même icône que près de l'horloge (barres + anneau or), en petit
            from PIL import ImageTk
            import interface
            self.logo = ImageTk.PhotoImage(interface.dessiner_icone(24, "pret", petite=True), master=self.root)
        except Exception:
            self.logo = None
        self.root.update_idletasks()
        hwnd = user32.GetParent(self.root.winfo_id())
        style = user32.GetWindowLongW(hwnd, -20)
        # NOACTIVATE | TOOLWINDOW (pas dans la barre des tâches) | TOPMOST | TRANSPARENT (clics traversants) | LAYERED
        user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x80 | 0x8 | 0x20 | 0x80000)
        self.x = (self.root.winfo_screenwidth() - self.L) // 2
        self.y = self.root.winfo_screenheight() - self.H - 70
        self.root.geometry(f"{self.L}x{self.H}+{self.x}+{self.y}")
        self.cachee = False
        self._animer()

    def montrer(self, etat):
        self.etat = etat  # lu par _animer dans le fil de tkinter

    def message(self, texte, alerte=False, duree=None):
        """« Corrigé : trompé », « introuvable »… : 4 s (6 s pour un problème), avec un son : Manu ne
        regarde pas toujours la pastille (23/10)."""
        self.msg = (texte, alerte, time.time() + (duree or (6 if alerte else 4)))
        if REGLAGES["son_messages"]:
            import winsound  # sons de Windows (volume système), joués sans bloquer
            winsound.PlaySound("SystemExclamation" if alerte else "SystemAsterisk",
                               winsound.SND_ALIAS | winsound.SND_ASYNC)

    def executer(self, fonction):
        """Depuis un autre fil (menu de l'icône) : la fonction tournera dans celui de tkinter."""
        self.taches.put(fonction)

    def _placer(self, largeur):
        if largeur != self.largeur or self.cachee:
            self.largeur = largeur
            self.canevas.config(width=largeur)
            x = (self.root.winfo_screenwidth() - largeur) // 2
            self.root.geometry(f"{largeur}x{self.H}+{x}+{self.y}")
            self.cachee = False

    def _pilule(self, c, fond, bord):
        r = self.H / 2
        x0, y0, x1, y1 = 1, 1, self.largeur - 1, self.H - 1
        for x in (x0, x1 - 2 * r):
            c.create_oval(x, y0, x + 2 * r, y1, fill=fond, outline=bord)
        c.create_rectangle(x0 + r, y0, x1 - r, y1, fill=fond, outline="")
        c.create_line(x0 + r, y0, x1 - r, y0, fill=bord)
        c.create_line(x0 + r, y1, x1 - r, y1, fill=bord)

    def _animer(self):
        while not self.taches.empty():
            try:
                self.taches.get_nowait()()
            except Exception as e:
                journal(f"Interface : {e}")
        etat = self.etat
        if self.msg and time.time() > self.msg[2]:
            self.msg = None
        if self.msg:
            texte, alerte, _ = self.msg
            self._placer(self.police.measure(texte) + 50)
            c = self.canevas
            c.delete("all")
            self._pilule(c, self.FOND, self.BORD)
            c.create_oval(16, self.H / 2 - 5, 26, self.H / 2 + 5, outline="#f39c12" if alerte else "#f2b705", width=2)
            c.create_text(36, self.H / 2, text=texte, anchor="w", fill="#f1f2f4", font=self.police)
            self.root.after(100, self._animer)
            return
        if etat == "cachee":
            if not self.cachee:
                self.root.geometry(f"{self.L}x{self.H}+-2000+-2000")
                self.cachee = True
            self.root.after(100, self._animer)
            return
        self._placer(self.L)
        c = self.canevas
        c.delete("all")
        self._pilule(c, self.FOND, self.BORD)
        self.phase += 0.25
        largeur, ecart = 3, 4
        # Icône à gauche (centre x = 22), barres centrées dans la place qui reste, point rouge à droite.
        gauche = 38 if self.logo else 0
        depart = gauche + (self.L - gauche - 28 - (self.BARRES * largeur + (self.BARRES - 1) * ecart)) / 2
        if self.logo:
            c.create_image(22, self.H / 2, image=self.logo)
        if etat in ("ecoute", "mains_libres", "commande"):
            niveau = min(1.0, math.sqrt(self.micro.niveau / 0.06))
            couleur = "#f2b705" if etat == "commande" else "#ffffff"  # barres or : mode commande (Alt)
        else:
            niveau = 0.0
            couleur = "#8e8e93" if etat == "chargement" else "#c7c7cc"
        for i in range(self.BARRES):
            centre = 1 - abs(i - (self.BARRES - 1) / 2) / (self.BARRES / 2)
            if etat in ("ecoute", "mains_libres", "commande"):
                cible = 3 + niveau * 20 * (0.45 + 0.55 * centre) * random.uniform(0.6, 1.0)
            else:  # vague qui ondule pendant le chargement et le calcul
                cible = 4 + 7 * (0.5 + 0.5 * math.sin(self.phase - i * 0.6))
            self.hauteurs[i] += (cible - self.hauteurs[i]) * 0.45
            h = self.hauteurs[i]
            x = depart + i * (largeur + ecart)
            c.create_rectangle(x, (self.H - h) / 2, x + largeur, (self.H + h) / 2, fill=couleur, outline="")
        if etat == "mains_libres":
            c.create_oval(self.L - 22, self.H / 2 - 4, self.L - 14, self.H / 2 + 4, fill="#e74c3c", outline="")
        self.root.after(33, self._animer)

    def boucle(self):
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass


# ---- Raccourcis ---------------------------------------------------------------

SC_CTRL = {29}
SC_WIN = {91, 92}
SC_ECHAP = 1
SC_MAJ = {42, 54}
SC_ALT = {56}  # Alt et AltGr : Ctrl+Win+Alt = mode commande (« retire », « corrige X par Y » sans « Djåse »)
# touche réelle de Windows pour vérifier qu'une touche notée « enfoncée » l'est encore (voir _purger) ;
# 541 : le Ctrl gauche que Windows fabrique pour AltGr
VK_DE_SC = {29: 0x11, 91: 0x5B, 92: 0x5C, 56: 0x12, 42: 0x10, 54: 0x10, 541: 0xA2}
CROCHET_SILENCE = 60    # s sans aucune touche reçue avant de vérifier le crochet clavier
CROCHET_RONDE = 20      # s entre deux passages du veilleur
PURGE_AGE = 2.0         # s : seule une touche notée depuis plus longtemps peut être oubliée (traitement en retard)
PURGE_AGE_INCONNUE = 10.0  # s : même chose pour une touche dont Windows ne donne pas l'état


def utilisateur_actif(depuis_ms, sauf_apres_ms):
    """Clavier ou souris touchés dans les `depuis_ms` dernières ms, en dehors de notre propre touche fantôme
    (envoyée à `sauf_apres_ms`, horloge GetTickCount) : la touche de contrôle n'est envoyée qu'à quelqu'un de présent,
    sinon elle empêcherait l'écran de s'éteindre et le PC de se verrouiller seul (relecture du 05/11/2026)."""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    li = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not user32.GetLastInputInfo(ctypes.byref(li)):
        return False
    maintenant = ctypes.windll.kernel32.GetTickCount()
    recent = (maintenant - li.dwTime) & 0xFFFFFFFF <= depuis_ms
    pas_la_fantome = (li.dwTime - sauf_apres_ms) & 0xFFFFFFFF > 1000 if sauf_apres_ms else True
    return recent and pas_la_fantome


def premier_plan_administrateur():
    """Fenêtre au premier plan d'un programme lancé en administrateur (Gestionnaire des tâches…) : Windows y bloque la
    touche fantôme (règle UIPI), le crochet paraîtrait mort à tort. Dans le doute (accès refusé) : oui."""
    k32, adv = ctypes.windll.kernel32, ctypes.windll.advapi32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return True
    try:
        jeton = ctypes.c_void_p()
        if not adv.OpenProcessToken(ctypes.c_void_p(h), 0x0008, ctypes.byref(jeton)):  # TOKEN_QUERY
            return True
        try:
            eleve, taille = ctypes.c_ulong(0), ctypes.c_ulong(0)
            ok = adv.GetTokenInformation(jeton, 20, ctypes.byref(eleve), 4, ctypes.byref(taille))  # TokenElevation
            return bool(eleve.value) if ok else True
        finally:
            k32.CloseHandle(jeton)
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


# Crochets clavier posés par la bibliothèque `keyboard` (poignée, fil) : elle ne les rend pas, on les relève au passage
# pour pouvoir décrocher l'ancien avant d'en poser un nouveau (jamais deux crochets vivants, relecture du 05/11/2026).
CROCHETS = []


def relever_crochets():
    import keyboard._winkeyboard as w
    if getattr(w, "_releve_djase", False):
        return
    original = w.SetWindowsHookEx

    def poser(*args):
        h = original(*args)
        if not h:
            journal("Crochet clavier refusé par Windows (SetWindowsHookEx) : Ctrl+Win ne marchera pas.")
        CROCHETS.append((h, ctypes.windll.kernel32.GetCurrentThreadId()))
        return h
    w.SetWindowsHookEx = poser
    w._releve_djase = True


def decrocher(h, fil):
    """Retire un crochet. Son fil reste endormi dans GetMessage, sans crochet ni effet : un message pour le réveiller
    ferait écrire Windows à l'adresse vide que la bibliothèque lui donne (accès mémoire invalide, vu à l'essai)."""
    if h:
        user32.UnhookWindowsHookEx(ctypes.c_void_p(h))


def masquer_menu_demarrer():
    """Touche fantôme (VK 0xE8, sans code) : Windows n'ouvre plus le menu Démarrer au relâchement de Win."""
    user32.keybd_event(0xE8, 0, 0, 0)
    user32.keybd_event(0xE8, 0, 2, 0)


class Raccourcis:
    def __init__(self, micro, pastille):
        self.micro = micro
        self.pastille = pastille
        self.enfoncees = set()
        self.t_touche = {}  # code → heure de son appui (voir _purger)
        self.mode = None  # None, "maintien", "mains_libres", "relache"
        self.t_appui = 0.0
        self.t_fantome = 0.0
        self.t_evenement = time.time()  # dernière touche reçue par le crochet (fantôme compris)

    def _masquer(self):
        self.t_fantome = time.time()
        masquer_menu_demarrer()

    def installer(self):
        import keyboard
        relever_crochets()  # avant le premier crochet, posé par keyboard.hook
        keyboard.hook(self._touche)
        threading.Thread(target=self._veiller_crochet, daemon=True, name="veilleur-crochet").start()

    # ---- Ctrl+Win perdu sans prévenir (vu par Manu le 01/11 et encore le 05/11/2026) ----------------------------
    # 1) Windows retire sans aucun message un crochet clavier bas niveau qui a répondu trop lentement (PC très chargé,
    #    disque mécanique) : plus aucune touche n'arrive à Djåse. Le veilleur s'en aperçoit (touche fantôme envoyée
    #    après une minute sans touche, quand Manu se sert du PC, jamais reçue), décroche l'ancien crochet et en repose
    #    un, sans relancer Djåse.
    # 2) Un relâchement de touche manqué (Win+L, fenêtre administrateur, PC chargé) laisse une touche « enfoncée » dans
    #    self.enfoncees : Ctrl+Win n'était plus jamais reconnu. _purger demande à Windows ce qui est vraiment enfoncé.

    def _veiller_crochet(self):
        import keyboard
        reposes = 0
        t_repose = 0.0  # dernier crochet reposé : pas un 2e tant que celui-ci n'a reçu aucune touche
        tick_fantome = 0  # GetTickCount de notre dernière touche de contrôle
        while True:
            time.sleep(CROCHET_RONDE)
            try:
                if time.time() - self.t_evenement < CROCHET_SILENCE:
                    continue
                if t_repose and self.t_evenement <= t_repose and time.time() - t_repose < 600:
                    continue  # le crochet reposé n'a encore rien reçu : pas d'autre avant 10 min (puis nouvel essai)
                if not utilisateur_actif(10_000, tick_fantome):
                    continue  # personne au PC : rien n'est envoyé (l'écran doit pouvoir s'éteindre, le PC se verrouiller)
                if ecran_verrouille() or premier_plan_administrateur():
                    continue  # Windows ne transmettrait pas la touche fantôme : le crochet paraîtrait mort à tort
                if keyboard._listener.queue.qsize() > 0:
                    continue  # des touches attendent d'être traitées : le crochet vit, le traitement est en retard
                envoi = time.time()
                tick_fantome = ctypes.windll.kernel32.GetTickCount()
                masquer_menu_demarrer()  # touche fantôme, sans effet ; le crochet la voit passer s'il est en vie
                time.sleep(3)
                if self.t_evenement >= envoi or keyboard._listener.queue.qsize() > 0 or ecran_verrouille():
                    continue
                reposes += 1
                t_repose = time.time()
                anciens = list(CROCHETS)
                CROCHETS.clear()
                for h, fil in anciens:
                    decrocher(h, fil)
                journal(f"Crochet clavier perdu (aucune touche reçue, même la touche de contrôle) : "
                        f"{len(anciens)} ancien(s) décroché(s), un nouveau posé (fois n° {reposes} depuis le lancement).")
                signaler("Raccourci Ctrl+Win rétabli")
                threading.Thread(target=keyboard._os_keyboard.listen, args=(keyboard._listener.direct_callback,),
                                 daemon=True, name=f"crochet-{reposes}").start()
            except Exception as e:  # le veilleur ne doit jamais mourir
                journal(f"Veilleur du crochet clavier : {e}")

    def _purger(self, sauf):
        """Oublie les touches notées enfoncées que Windows ne voit plus enfoncées (relâchement manqué). Seulement une
        touche notée depuis plus de PURGE_AGE : le fil de traitement peut être en retard sur le clavier réel, et un
        Ctrl bref (appui court de Ctrl+Win) aurait été oublié à tort (relecture du 05/11/2026)."""
        maintenant = time.time()
        for sc in list(self.enfoncees):
            if sc == sauf:
                continue
            age = maintenant - self.t_touche.get(sc, 0.0)
            vk = VK_DE_SC.get(sc) or user32.MapVirtualKeyW(sc, 3)  # 3 : code de touche vers touche virtuelle
            oublier = (age > PURGE_AGE and not touche_tenue(vk)) if vk else age > PURGE_AGE_INCONNUE
            if oublier:
                self.enfoncees.discard(sc)
                self.t_touche.pop(sc, None)
                journal(f"Touche {sc} oubliée : son relâchement n'était pas arrivé à Djåse.")
        if self.mode == "relache" and not self.enfoncees & (SC_CTRL | SC_WIN | SC_MAJ | SC_ALT):
            self.mode = None

    def _combo(self):
        return bool(self.enfoncees & SC_CTRL) and bool(self.enfoncees & SC_WIN) \
            and self.enfoncees <= SC_CTRL | SC_WIN | SC_ALT

    def _mode_commande(self):
        """Alt avec Ctrl+Win (avant ou pendant) : la dictée est une commande, sans « Djåse » à dire."""
        s = self.micro.session
        if s is not None and not s.commande:
            s.commande = True
            self._masquer()  # touche fantôme : le relâchement d'Alt n'ouvre pas le menu de la fenêtre
            self.pastille.montrer("commande")

    def _touche(self, ev):
        self.t_evenement = time.time()  # avant tout filtre : la touche fantôme du veilleur compte
        sc = ev.scan_code
        if not sc or sc < 0:
            return  # notre touche fantôme (pas de vrai code : 0, ou -232 pour VK 0xE8)
        if sc not in SC_CTRL | SC_WIN | SC_ALT and time.time() - self.t_fantome < 0.15:
            return  # notre touche fantôme, si Windows lui a donné un code (Alt pressé en même temps : gardé)
        if ETAT["pause"] and self.mode is None:
            if ev.event_type == "down":
                collage_touche()  # tapé pendant la pause : « corrige » n'effacera plus le dernier collage
            return  # en pause (menu de l'icône) : Ctrl+Win redevient un raccourci ordinaire
        if ev.event_type == "down":
            if sc not in SC_CTRL | SC_WIN | SC_ALT:
                collage_touche()  # une vraie touche : le dernier collage n'est plus effaçable
            if sc in self.enfoncees:
                return  # répétition automatique
            self._purger(sauf=sc)
            self.enfoncees.add(sc)
            self.t_touche[sc] = time.time()
            if self.mode is None and self._combo():
                self._masquer()
                self.mode = "maintien"
                self.t_appui = time.time()
                self.micro.demarrer()
                self.pastille.montrer("ecoute")
                if self.enfoncees & SC_ALT:
                    self._mode_commande()
            elif self.mode in ("maintien", "mains_libres") and sc in SC_ALT:
                self._mode_commande()
            elif self.mode == "maintien" and sc in SC_MAJ:
                self._fin(annuler=True)
                self.mode = "relache"
                self._recoller()
            elif self.mode == "maintien" and sc not in SC_CTRL | SC_WIN:
                self._fin(annuler=True)
                journal(f"Annulé : touche « {ev.name} » (code {sc}) pressée pendant Ctrl+Win.")
            elif self.mode == "mains_libres":
                if sc == SC_ECHAP:
                    self._fin(annuler=True)
                    journal("Mains libres annulé (Échap).")
                elif self._combo():
                    self._masquer()
                    self._fin()
                    self.mode = "relache"
        else:
            self.enfoncees.discard(sc)
            self.t_touche.pop(sc, None)
            if self.mode == "maintien" and sc in SC_CTRL | SC_WIN:
                if time.time() - self.t_appui < APPUI_BREF:
                    self.mode = "mains_libres"
                    self.pastille.montrer("mains_libres")
                    journal("Mains libres : Ctrl+Win pour finir, Échap pour annuler.")
                else:
                    self._fin()
            elif self.mode == "relache" and not self.enfoncees & (SC_CTRL | SC_WIN | SC_MAJ | SC_ALT):
                self.mode = None

    def _recoller(self):
        texte = DERNIERE[0]
        if not texte:
            journal("Rien à recoller : aucune dictée pour l'instant.")
            signaler("Rien à recoller", alerte=True)
            return
        journal(f"Recollé (Ctrl+Win puis Maj) : {texte}")
        threading.Thread(target=coller, args=(texte,), daemon=True).start()  # attend le relâchement

    def _fin(self, annuler=False):
        self.mode = None
        s = self.micro.arreter(annuler)
        if s is None or s.annulee:
            self.pastille.montrer("cachee")
        else:
            self.pastille.montrer("travail")


# ---- Assemblage ---------------------------------------------------------------

# « Djåse, corrige trempé par trompé (et retiens) » : Parakeet entend « jazz » pour Djåse.
COMMANDE_CORRIGE = re.compile(r"^\W*(?:dj[åa]s?[ez]?|ja[sz]{1,2}e?|g[iy]?a[sz]e?|dia[sz]|j(?:e|\W{0,2})s{1,2})\W+(?:corrig|rempla[cç]|replac)\w*\s+(?:dans la phrase\s+)?"
                              r"(.+?)\s+par\s+(.+?)(\W+et\s+reti\w*)?\W*$", re.I)


# « Djåse, efface » (dernier mot), « Djåse, efface tout » (dernière dictée), « Djåse, efface chat ».
# Toujours avec Djåse devant (choix de Manu) : « Efface le tableau » dicté reste une phrase.
# Parakeet entend « efface » : F, F us, F S, Fast (essai de Manu du 23/10, « efface tout » → « F us two »).
# Puis (07h46) : EFAS, « e fa stu », « Gias » pour Djåse. « efface » est trop mal entendu : on accepte
# aussi supprime / enlève / retire / annule, que Manu choisira selon ce que Parakeet comprend le mieux.
# « Djåse, retire » enchaîné : entendu « Je se retire » / « Ja se retire » (08h35, jamais du vrai français).
# « Jess », « JS », « J s » : Djåse entendu en lettres (essai de Manu du 23/10, 19h21).
COMMANDE_EFFACE = re.compile(r"^\W*(?:dj[åa]s?[ez]?|ja[sz]{1,2}e?|g[iy]?a[sz]e?|dia[sz]|gi[àa]|j[ea]\s+se|j(?:e|\W{0,2})s{1,2})\W+(?:e\W+)?"
                             r"(?:é?eff?a\w*|é?fa(?:c|s)\w*|fast|e\W+fa\w*|et\W+fa(?:c|s)\w*|[eé]?phas\w*"  # Et face, Ephas
                             r"|f(?:\W+(?:us|s|as|ace|asse))?"
                             r"|supprim\w*|suprem\w*|enl[eè]v\w*|olive|retir\w*|annul\w*|anul\w*|[au]n\W+nul\w*)"
                             r"(?!\w)\W*(.*?)\W*$", re.I)
# Calibrage du 23/10 (Manu dit chaque mot 3 fois, Parakeet et Canary) : « retire » 6/6, le plus sûr ;
# supprime → Supreme/Supprim ; enlève → « Olive » chez les deux, toujours ; annule → An nul / Un nul ;
# efface → I've got it / Effects / E fast (Parakeet 0/3, Canary 2/3) ; tout → Two / tu ; Djåse → Jazz.


def commande_efface(texte):
    """Renvoie "dernier", "tout" ou le mot à effacer ; None si ce n'est pas la commande."""
    # « (e) Fastu », « Ephasu », « et Fastu » = efface tout (23/10).
    texte = re.sub(r"\b(?:et?\W+|[eé])?(?:f|ph)as\s*t?u\b", "efface tout", texte or "", flags=re.I)
    m = COMMANDE_EFFACE.match(texte)
    if not m:
        return None
    reste = m.group(1).strip(" «»\"'.,").lower()
    if reste in ("", "le dernier mot", "dernier mot", "ça", "ca", "le mot", "un mot"):
        return "dernier"
    # « tout » : aussi « toute la ligne », « la phrase », et ce qu'en fait Parakeet (two, stu, a stu…).
    if len(reste.split()) <= 4 and re.search(r"\b(?:tout\w*|tous|two|too|to|tu|stu|sto|stuff|ligne|phrase|dictée)\b", reste):
        return "tout"
    mot = re.sub(r"^(?:le mot|le|la|les|l['’])\s+", "", m.group(1).strip(" «»\"'.,"), flags=re.I)
    return mot.strip(" «»\"'.,") or "dernier"


def effacer(cible):
    """« dernier » / « tout » : retours arrière sur le dernier collage (seulement s'il est intact) ;
    un mot : comme « corrige X par rien »."""
    import keyboard
    if cible not in ("dernier", "tout"):
        return corriger_dernier(cible, "", False)
    ancien = DERNIERE[0]
    if not (COLLAGE["intact"] and COLLAGE["texte"] == ancien and ancien):
        if cible == "dernier":  # Djåse ne sait plus ce qu'il y a avant le curseur : raccourci Windows
            attendre_relachement()
            COLLAGE["muet"] = time.time() + 0.5
            keyboard.send("ctrl+backspace")
            journal("Effacé : le mot avant le curseur (Ctrl+Retour arrière, tapé ou cliqué depuis le collage).")
            signaler("Effacé")
        else:
            journal("Rien effacé : tapé ou cliqué depuis le collage, Djåse ne sait plus où est le curseur.")
            signaler("Rien effacé (curseur déplacé)", alerte=True)
        return None
    if cible == "tout":
        nouveau = ""
    else:
        m = re.search(r"\w[\w'’-]*\W*$", ancien)  # le dernier mot avec sa ponctuation (« chemin. », « va ? »)
        nouveau = ancien[:m.start()] if m else ""
    n = len(ancien) - len(nouveau)
    attendre_relachement()
    COLLAGE["muet"] = time.time() + 1.0 + n * 0.02
    for _ in range(n):
        keyboard.send("backspace")
        time.sleep(0.005)
    DERNIERE[0] = nouveau
    COLLAGE.update(texte=nouveau, bloc=COLLAGE["bloc"][:len(COLLAGE["bloc"]) - n], intact=True)
    journal(f"Effacé ({'toute la dictée' if cible == 'tout' else 'dernier mot'}) : « {ancien[len(nouveau):]} »")
    signaler("Effacé")
    return nouveau


# ---- Gras, italique, barré, citation (demande de Manu du 23/10, 19h32) ----
# Djåse colle du texte brut : chaque appli a sa façon d'écrire la mise en forme (choix de Manu : selon l'appli).
FORMES_WHATSAPP = {"gras": ("*", "*"), "italique": ("_", "_"), "barre": ("~", "~")}
FORMES_MARKDOWN = {"gras": ("**", "**"), "italique": ("*", "*"), "barre": ("~~", "~~")}  # Obsidian, Discord, GitHub…
# Applis de texte enrichi : les marques s'afficheraient telles quelles, on colle le texte seul (citation entre « »).
SANS_MARQUES = ("winword.exe", "outlook.exe", "olk.exe", "excel.exe", "powerpnt.exe", "thunderbird.exe")
# « italic » : Parakeet l'écrit à l'anglaise (essai de Manu, 23/10 20h17).
NOMS_FORME = {"gras": r"gras", "italique": r"itali(?:que|c)s?", "barre": r"barr[ée]e?s?"}
# « en » entendu « on » ; « fin du gras » entendu « faire du gras », « enfin du gras », « fad du barré »
# (Parakeet et Canary, 23/10 20h17) : seulement APRÈS une ouverture, donc « faire du gras » seul reste intact.
OUVRE_FORME = r"(?:mets?\s+)?(?:en|on|au)\s+"  # « au gras » (les deux moteurs, 20h40) : seulement suivi d'une fin
FIN_FORME = r"(?:en)?(?:fin|faire|fait|fad)\s+(?:du\s+|de\s+l['’]\s*|de\s+|d['’]\s*)?"
FORME_SEULE = re.compile(r"^\W*(?:(?:dj[åa]s?[ez]?|ja[sz]{1,2}e?)\W+)?(tout\s+)?(?:mets?\s+)?(?:en|on)\s+"
                         r"(gras|itali(?:que|c)s?|barr[ée]e?s?|citation)\W*$", re.I)


def marques(programme, titre):
    """Marques de mise en forme de l'appli qui reçoit le texte ; None : appli de texte enrichi (Word, Gmail…)."""
    titre = titre.lower()
    if "whatsapp" in programme or "whatsapp" in titre:
        return FORMES_WHATSAPP
    if programme in SANS_MARQUES or "gmail" in titre or "outlook" in titre:
        return None
    return FORMES_MARKDOWN


def _entourer(texte, forme, m):
    """« ceci est important. » → « **ceci est important**. » : la ponctuation finale reste dehors."""
    # Le tiret d'un élément de liste et le « > » d'une citation restent devant les marques.
    tete, corps, fin = re.match(r"((?:[->]\s+)?)(.*?)([.!?,;:]*)$", texte.strip(), re.S).groups()
    corps = corps.rstrip()
    if not corps:
        return texte
    if fin[:1] in ("?", "!"):  # typographie française : « *vraiment* ? »
        fin = " " + fin
    if forme == "citation":
        return f"> {tete}{corps}{fin}" if m is not None else f"{tete}« {corps} »{fin}"
    return f"{tete}{m[forme][0]}{corps}{m[forme][1]}{fin}" if m is not None else tete + corps + fin


def mise_en_forme(texte, programme, titre):
    """Dans la dictée : « en gras ceci est important fin du gras », pareil pour l'italique et le barré ;
    « citation … fin de citation » ; une dictée qui commence par « citation » (pas « citation de … »)."""
    m = marques(programme, titre)

    def remplacer(forme):
        def faire(r):
            debut = r.start() == 0 or re.search(r"[.!?\n]\s*$", r.string[:r.start()])
            # La ponctuation juste avant « fin du … » est un faux arrêt (Canary : « 25 euros. Faire du barré, 30 ») :
            # celle d'après fait foi. On garde seulement ? et !.
            corps = r.group(1).strip(" ,").rstrip(" ,.;:")
            if debut:
                corps = corps[:1].upper() + corps[1:]
            entoure = _entourer(corps, forme, m)
            return ("\n" + entoure + "\n") if forme == "citation" and m is not None else " " + entoure + " "
        return faire
    for forme, nom in NOMS_FORME.items():
        texte = re.sub(rf"[\s,]*\b{OUVRE_FORME}{nom}\b[\s,:]*(.+?)[\s,]*\b{FIN_FORME}"
                       rf"{nom}\b\s*", remplacer(forme), texte, flags=re.I | re.S)  # la virgule d'après reste
    texte = re.sub(r"[\s,]*\bcitation\b[\s,:]*(.+?)[\s,]*\b(?:en)?(?:fin|faire|fait|fad)\s+de\s+(?:la\s+)?citation\b\s*",
                   remplacer("citation"),
                   texte, flags=re.I | re.S)
    debut = re.match(r"\W*citation\b(?!\s+(?:de|d['’]|du|des)\b)[\s,.:]*(.+)$", texte, re.I | re.S)  # « Citation. Le… »
    if debut and debut.group(1).strip():
        corps = debut.group(1).strip()
        texte = _entourer(corps[:1].upper() + corps[1:], "citation", m)
    texte = re.sub(r"[ \t]{2,}", " ", texte)
    texte = re.sub(r" +([.,])(?!\.)", r"\1", texte)  # « **important** . » → « **important**. » (pas « ... »)
    texte = re.sub(r" *\n *", "\n", texte)
    return re.sub(r"\n[,;:]\s*", "\n", texte).strip(" ")  # après une citation en bloc : pas de ligne « , et… »


def commande_forme(texte):
    """« en gras » dit seul → ("gras", False) : le dernier mot ; « tout en gras » → ("gras", True)."""
    r = FORME_SEULE.match(texte or "")
    if not r:
        return None
    nom = r.group(2).lower()
    forme = "citation" if nom == "citation" else "gras" if nom == "gras" else "italique" if nom.startswith("ital") \
        else "barre"  # (« italic » compris)
    return forme, bool(r.group(1)) or forme == "citation"


def formater_dernier(forme, tout):
    """Met en forme le dernier mot (ou toute la dernière dictée) déjà collé : retours arrière puis collage,
    comme « corrige ». Seulement si rien n'a été tapé ni cliqué depuis."""
    import keyboard
    ancien = DERNIERE[0]
    if not (COLLAGE["intact"] and COLLAGE["texte"] == ancien and ancien.strip()):
        journal("Rien mis en forme : tapé ou cliqué depuis le collage, Djåse ne sait plus où est le curseur.")
        signaler("Rien mis en forme (curseur déplacé)", alerte=True)
        return
    m = marques(*appli_devant(COLLAGE["fenetre"]))
    if m is None and forme != "citation":
        journal("Pas de mise en forme : cette appli (texte enrichi) afficherait les marques telles quelles.")
        signaler("Pas de gras ici : utilise Ctrl+G", alerte=True)
        return
    if tout or forme == "citation":
        nouveau = "\n".join(_entourer(l, forme, m) if l.strip() else l for l in ancien.split("\n"))
    else:
        r = re.search(r"([\w'’-]+)(\W*)$", ancien)
        if not r:
            return
        nouveau = ancien[:r.start()] + _entourer(r.group(1), forme, m) + r.group(2)
    attendre_relachement()
    COLLAGE["muet"] = time.time() + 1.0 + len(ancien) * 0.02
    for _ in range(len(ancien)):
        keyboard.send("backspace")
        time.sleep(0.005)
    COLLAGE["bloc"] = COLLAGE["bloc"][:len(COLLAGE["bloc"]) - len(ancien)]
    coller(nouveau, continuer=True)
    DERNIERE[0] = nouveau
    journal(f"Mis en forme ({forme}{', toute la dictée' if tout else ', dernier mot'}) : {nouveau}")
    signaler("Mis en forme")


def commande_corrige(texte):
    m = COMMANDE_CORRIGE.match(texte or "")
    if not m:
        return None
    nettoie = lambda t: t.strip(" «»\"'.,")
    return nettoie(m.group(1)), nettoie(m.group(2)), bool(m.group(3))


def remplacer_mot(texte, faux, juste):
    """Remplace « faux » (mot ou groupe de mots entier) ; majuscule gardée en début de phrase.
    Un petit mot (du, tu, que…) revient souvent ailleurs : seulement le premier."""
    def un(m):
        return juste[:1].upper() + juste[1:] if m.group(0)[:1].isupper() and juste[:1].islower() else juste
    return re.subn(r"(?<!\w)" + re.escape(faux) + r"(?!\w)", un, texte, count=0 if len(faux) >= 4 else 1,
                   flags=re.I)


# Fins qui se prononcent « é » : trempé, tremper, trempez… sont le même son pour Parakeet et Grammalecte.
FINS_E = ("ées", "és", "ée", "é", "er", "ez", "ais", "ait", "ai")


def meme_son(texte, faux, juste):
    """« tremper » absent mais « trempé » présent : renvoie (trempé, trompé), fin du texte gardée."""
    fin = next((f for f in FINS_E if faux.lower().endswith(f) and len(faux) > len(f) + 1), None)
    if not fin or " " in faux:
        return None
    racine = faux[:-len(fin)]
    m = re.search(r"(?<!\w)" + re.escape(racine) + "(" + "|".join(FINS_E) + r")(?!\w)", texte, re.I)
    if not m:
        return None
    fin_juste = next((f for f in FINS_E if juste.lower().endswith(f) and len(juste) > len(f) + 1), None)
    return m.group(0), (juste[:-len(fin_juste)] + m.group(1) if fin_juste else juste)


# Petits mots qui se prononcent pareil : « Djåse, efface au » est entendu « efface o » (23/10).
HOMOPHONES = [{"o", "ô", "oh", "ho", "au", "aux", "eau", "eaux", "haut", "ou"}, {"a", "à", "ah", "as"},
              {"et", "est", "ai", "es", "hé", "eh"}, {"ce", "se"}, {"ces", "ses", "c'est", "s'est"},
              {"ça", "sa", "ca"}, {"on", "ont"}, {"son", "sont"}, {"mais", "mes", "met", "mets"},
              {"la", "là", "l'a"}, {"peu", "peut", "peux"}, {"vers", "vert", "verre", "ver"},
              {"du", "dû"}, {"ou", "où", "houx"}, {"leur", "leurs"}, {"quel", "quelle", "quels", "quelles"}]


def homophone_present(texte, faux):
    """Un mot de la phrase qui se prononce comme « faux » (le premier trouvé), sinon None."""
    for groupe in HOMOPHONES:
        if faux.lower() in groupe:
            for mot in re.findall(r"\w+(?:['’]\w+)?", texte):
                if mot.lower().replace("’", "'") in groupe and mot.lower() != faux.lower():
                    return mot
    return None


def mot_proche(texte, faux):
    """« Nat » dit dans la commande, « natt » dans la phrase : Parakeet n'entend pas deux fois pareil.
    Le mot (un seul) le plus ressemblant, même première lettre, ressemblance d'au moins 75 %."""
    import difflib
    if " " in faux.strip():
        return None
    notes = {}
    for mot in set(re.findall(r"\w+", texte)):
        r = difflib.SequenceMatcher(None, faux.lower(), mot.lower()).ratio()
        if mot[:1].lower() == faux[:1].lower() and r >= 0.75:
            notes[mot] = r
    if not notes:
        return None
    meilleurs = sorted(notes.items(), key=lambda x: -x[1])
    if len(meilleurs) > 1 and meilleurs[1][1] == meilleurs[0][1]:
        return None  # deux candidats aussi proches : on ne devine pas
    return meilleurs[0][0]


def retenir_correction(faux, juste):
    """Ajoute « faux → juste » en tête des remplacements de dictionnaire.json (format gardé)."""
    fichier = DONNEES / "dictionnaire.json"
    try:
        # Par json (comme la fenêtre) : l'ancienne insertion de texte cassait le fichier quand
        # « remplacements » était vide (virgule finale, relecture GLM du 23/10).
        donnees = json.loads(fichier.read_text(encoding="utf-8"))
        anciens = {k: v for k, v in donnees.get("remplacements", {}).items() if k != faux.lower()}
        donnees["remplacements"] = {faux.lower(): juste, **anciens}
        fichier.write_text(json.dumps(donnees, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        journal(f"  Retenu dans le dictionnaire : « {faux} » → « {juste} ».")
    except (OSError, ValueError) as e:
        journal(f"  Pas retenu (dictionnaire.json : {e}).")


def chercher_correction(ancien, faux, juste):
    """Applique « faux → juste » à ancien. Renvoie (nouveau, trouvé, faux, juste, note) sans rien coller."""
    note = ""
    signe = PONCTUATION_SEULE.get(faux.lower()) if not juste else None
    if signe and len(signe) == 1 and signe in ",.;:?!":
        # « Djåse, efface la virgule » : le dernier signe de la dictée, pas le mot « virgule ».
        i = ancien.rfind(signe)
        nouveau, n = (ancien[:i] + ancien[i + 1:], 1) if i >= 0 else (ancien, 0)
    else:
        # -é / -er / -ez ne s'entendent pas : on garde toujours la fin du mot déjà dans la phrase.
        faux, juste = meme_son(ancien, faux, juste) or (faux, juste)
        nouveau, n = remplacer_mot(ancien, faux, juste)
        if not n:
            proche = homophone_present(ancien, faux)
            pourquoi = "se prononce pareil"
            if not proche:
                proche, pourquoi = mot_proche(ancien, faux), "est le mot le plus proche"
            if proche:
                note = f"  « {faux} » pas trouvé tel quel : « {proche} » {pourquoi}."
                faux = proche
                nouveau, n = remplacer_mot(ancien, faux, juste)
    if n and juste and faux.lower() == juste.lower():
        return ancien, 0, faux, juste, f"  « {faux} » est déjà écrit ainsi dans la phrase : rien à corriger."
    if n and not juste:  # « Djåse, efface chat » : pas de « le  chat » ni d'espace devant une virgule
        nouveau = re.sub(r"(?<![.?!]) {2,}", " ", nouveau)
        # Phrase entière effacée : « là ? . » ou « pense. . » → un seul signe (pas les « ... »).
        nouveau = re.sub(r"([?!]|(?<!\.)\.)\s+\.(?!\.)", r"\1", nouveau)
        nouveau = re.sub(r" +(,|\.(?!\.))", r"\1", nouveau).strip(" ")  # pas d'espace avant « , » « . »
        nouveau = re.sub(r",(\s*[.?!])", r"\1", nouveau)  # « mignon, vraiment. » → « mignon. »
        nouveau = re.sub(r"((?<!\.)[.?!]\s+)([a-zà-ÿ])", lambda m: m.group(1) + m.group(2).upper(), nouveau)
        nouveau = nouveau[:1].upper() + nouveau[1:] if ancien[:1].isupper() else nouveau
    return nouveau, n, faux, juste, note


def corriger_dernier(faux, juste, retenir):
    """Cherche d'abord dans la dernière dictée, puis dans le bloc des dictées collées d'affilée
    (« Le chat est vraiment beau » + « . » puis « Djåse, efface vraiment »)."""
    import keyboard
    texte_vise = COLLAGE["bloc"] if COLLAGE["intact"] else DERNIERE[0]
    if faux.lower() == juste.lower() and homophone_present(texte_vise, faux):
        # « corrige sa par ça » entendu « corrige ça par ça » : le mot de la phrase qui se prononce pareil.
        faux = homophone_present(texte_vise, faux)
    if faux.lower() == juste.lower():
        journal(f"Correction : « {faux} » et « {juste} » sont le même mot, rien à corriger "
                "(mot mal entendu ? l'ajouter au dictionnaire).")
        signaler("Même mot des deux côtés", alerte=True)
        return None
    derniere, bloc = DERNIERE[0], COLLAGE["bloc"]
    intact = COLLAGE["intact"] and COLLAGE["texte"] == derniere and bloc.endswith(derniere)
    ancien, sur_bloc = derniere, False
    nouveau, n, f, j, note = chercher_correction(derniere, faux, juste)
    if not n and intact and bloc != derniere and len(bloc) <= 1500:
        nouveau, n, f, j, note = chercher_correction(bloc, faux, juste)
        ancien, sur_bloc = bloc, True
    if not n and "déjà écrit" in note:
        journal(f"Correction :{note}")
        signaler(f"Déjà écrit « {f} »", alerte=True)
        return None
    if not n:
        journal(f"Correction : « {faux} » introuvable dans la dernière dictée : {derniere}")
        signaler(f"« {faux} » introuvable", alerte=True)
        return None
    faux, juste = f, j
    if note:
        journal(note)
    DERNIERE[0] = nouveau
    autres = len(re.findall(r"(?<!\w)" + re.escape(faux) + r"(?!\w)", nouveau, flags=re.I)) if juste else 0
    if autres:
        journal(f"  Seul le premier « {faux} » est corrigé ({autres} autre(s) laissé(s) tel(s) quel(s)).")
    if retenir:
        retenir_correction(faux, juste)
    if intact:
        attendre_relachement()
        COLLAGE["muet"] = time.time() + 1.0 + len(ancien) * 0.02
        for _ in range(len(ancien)):
            keyboard.send("backspace")
            time.sleep(0.005)
        if not sur_bloc:  # la dernière dictée seulement : le reste du bloc est toujours là
            COLLAGE["bloc"] = bloc[:len(bloc) - len(derniere)]
        coller(nouveau, continuer=not sur_bloc)
        journal(f"Corrigé (« {faux} » → « {juste} ») : {nouveau}" if juste else f"Effacé « {faux} » : {nouveau}")
        signaler(f"Corrigé : {juste}" if juste else f"Effacé : {faux}")
    else:
        journal(f"Correction gardée (tapé ou cliqué depuis le collage, rien effacé) : {nouveau}")
        journal("  Ctrl+Win puis Maj pour la coller.")
        signaler("Gardée · Ctrl+Win, Maj pour coller")
    return nouveau


def fenetre_changee(session):
    """La fenêtre au premier plan n'est plus celle du relâchement des touches (Alt-Tab pendant le calcul)."""
    return bool(session.fenetre) and user32.GetForegroundWindow() != session.fenetre


def fin_dictee(pastille, coller_texte=True):
    def sortie(texte, session):
        attente = time.time() - session.fin
        duree = session.fin - session.debut
        # Avant Grammalecte d'abord : il change « par trompé » en « par tromper ».
        brut = regles(session.brut)
        if session.commande:  # Ctrl+Win+Alt : « retire tout » vaut « Djåse, retire tout »
            brut, texte_appel = "Djåse, " + brut, "Djåse, " + texte
        else:
            texte_appel = texte
        commande = commande_corrige(brut) or commande_corrige(texte_appel)
        if commande:
            pastille.montrer("cachee") if pastille else None
            journal(f"Commande : corrige « {commande[0]} » par « {commande[1]} »"
                    + (" et retiens." if commande[2] else "."))
            nouveau = corriger_dernier(*commande) if coller_texte else None
            if nouveau:
                if REGLAGES["historique"]:
                    with open(DONNEES / "historique.jsonl", "a", encoding="utf-8") as f:
                        f.write(json.dumps({"date": datetime.now().isoformat(timespec="seconds"),
                                            "correction": list(commande[:2]), "commande": texte,
                                            "texte": nouveau}, ensure_ascii=False) + "\n")
            else:
                journal("  Rien collé : une commande ne se colle jamais.")  # proposition du 23/10, acceptée par Manu
            return
        cible = commande_efface(brut) or commande_efface(texte_appel)
        if cible:
            pastille.montrer("cachee") if pastille else None
            journal(f"Commande : efface {'le dernier mot' if cible == 'dernier' else 'tout' if cible == 'tout' else f'« {cible} »'}.")
            if coller_texte:
                effacer(cible)
            return
        forme = commande_forme(brut)
        if forme:  # « en gras » / « tout en gras » / « en citation » dit seul : la dictée d'avant
            pastille.montrer("cachee") if pastille else None
            journal(f"Commande : {'tout ' if forme[1] else ''}en {forme[0]}.")
            if coller_texte:
                formater_dernier(*forme)
            return
        if session.commande and texte != TOUCHE_ENTREE:
            # Mode commande : jamais collé, même si la commande n'est pas comprise.
            pastille.montrer("cachee") if pastille else None
            journal(f"Commande non comprise (Ctrl+Win+Alt) : « {session.brut} ». Rien collé.")
            signaler("Commande non comprise", alerte=True)
            return
        if texte == TOUCHE_ENTREE:
            pastille.montrer("cachee") if pastille else None
            if coller_texte and fenetre_changee(session):
                journal("Touche Entrée PAS envoyée : la fenêtre a changé pendant le calcul.")
                signaler("Fenêtre changée · Entrée pas envoyée", alerte=True)
                return
            if coller_texte:
                import keyboard
                attendre_relachement()
                COLLAGE["muet"] = time.time() + 0.5
                keyboard.send("enter")
                COLLAGE["intact"] = False  # message envoyé : « corrige » n'efface plus rien
            journal(f"Touche Entrée (dicté : « {session.brut} »).")
            return
        if texte == "." and COLLAGE["intact"] and FIN_DE_PHRASE.search(COLLAGE["texte"]):
            journal("  « point » ignoré : la phrase précédente finit déjà par un point.")
            texte = ""  # sinon « trois.. » (le style formel avait déjà mis le point)
        if texte and coller_texte and COLLAGE["intact"] and COLLAGE["bloc"].rstrip(" ") \
                and COLLAGE["bloc"].rstrip(" ")[-1] not in ".?!…\n":
            # Pas de point à la fin de la dictée d'avant : la phrase continue (demande de Manu du 24/10).
            # La fin du bloc collé, pas la dernière dictée : après « efface tout », celle-ci est vide (24/10 02h50).
            texte = minuscule_de_suite(texte)
        if texte:
            DERNIERE[0] = texte
        if texte and coller_texte and fenetre_changee(session):
            # Alt-Tab pendant le calcul : coller ailleurs serait pire que ne rien coller (relecture GLM, 23/10).
            import pyperclip
            pyperclip.copy(texte)
            COLLAGE["intact"] = False
            journal("  Fenêtre changée pendant le calcul : rien collé, texte mis dans le presse-papier.")
            signaler("Fenêtre changée · Ctrl+V pour coller", alerte=True)
        elif texte and coller_texte:
            # Deux dictées à la suite (rien tapé ni cliqué entre) : une espace entre « chemin. » et la suite.
            # « : ; ? ! » et « ... » dictés seuls prennent aussi l'espace (typographie française,
            # « ... » espacés chez Manu), pas « , . ».
            # « espace » dicté (texte d'espaces seulement) : rien d'ajouté devant, sinon 3 espaces (23/10).
            suite = COLLAGE["intact"] and COLLAGE["texte"][-1:] not in ("", " ", "\n", "\t") \
                and texte.strip(" ") != "" and (texte.startswith("...") or texte[:1] not in ",.)]}»…\n")
            espace = "  " if REGLAGES["double_espace"] and FIN_DE_PHRASE.search(COLLAGE["texte"]) else " "
            coller(texte, espace if suite else "", continuer=True)
        pastille.montrer("cachee") if pastille else None
        journal(f"{duree:.1f} s de dictée → collé {attente:.1f} s après le relâchement "
                f"({session.moteur} {session.t_whisper:.1f} s, nettoyage {session.t_nettoyage:.1f} s au total)")
        journal(f"  {texte}")
        if session.tout:  # pour régler le gain de la carte son : viser -12 dB environ, jamais 0
            crete = float(np.abs(np.concatenate(session.tout)).max())
            journal(f"  Niveau du micro : {20 * math.log10(max(crete, 1e-6)):.0f} dB "
                    + ("(bas : monter le gain de la carte son)" if crete < 0.1 else
                       "(trop fort : baisser le gain)" if crete > 0.9 else "(bien)"))
        if not texte:  # (un saut de ligne seul, « \n », est bien collé)
            journal(f"  (rien collé ; Parakeet avait entendu : « {session.brut} »)")
        if session.grammaire:
            journal("  Grammaire : " + ", ".join(f"{a} → {b}" for a, b in session.grammaire))
        # Dictée vide gardée aussi (texte "") : son audio sert à comprendre ce que Parakeet a raté.
        if REGLAGES["historique"] and (texte or session.tout):
            maintenant = datetime.now()
            fichier = None
            if REGLAGES["audio"] and session.tout:
                try:
                    import soundfile as sf
                    (DONNEES / "audio").mkdir(exist_ok=True)
                    fichier = maintenant.strftime("%Y-%m-%d_%H-%M-%S") + ".wav"
                    sf.write(DONNEES / "audio" / fichier, np.concatenate(session.tout), RATE, subtype="PCM_16")
                except Exception as e:
                    journal(f"Audio non sauvegardé ({e}).")
                    fichier = None
            with open(DONNEES / "historique.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"date": maintenant.isoformat(timespec="seconds"),
                                    "duree": round(duree, 1), "attente": round(attente, 1),
                                    "audio": fichier, "morceaux": session.journal,
                                    "grammaire": session.grammaire, "appli": session.appli[0],
                                    "moteur": session.moteur,
                                    "style": session.style, "texte": texte},
                                   ensure_ascii=False) + "\n")
    return sortie


def tester(fichier):
    """Rejoue un enregistrement en temps réel, comme si on dictait, puis mesure l'attente finale."""
    import soundfile as sf
    nettoyeur = Nettoyeur()
    nettoyeur.demarrer()
    fini = threading.Event()
    rappel = fin_dictee(None, coller_texte=False)

    def sortie(texte, session):
        rappel(texte, session)
        fini.set()
    transcripteur = Transcripteur(nettoyeur, sortie)
    micro = Micro(transcripteur)
    transcripteur.pret.wait()
    if REGLAGES["nettoyage"] and nettoyeur.proc:
        nettoyeur.pret.wait(180)
    audio, _ = sf.read(fichier, dtype="float32")
    audio = audio.reshape(-1, 1) if audio.ndim == 1 else audio
    journal(f"Rejeu de {fichier} ({len(audio) / RATE:.0f} s) en temps réel...")
    for _ in range(10):
        micro.recevoir(np.zeros((BLOCK, audio.shape[1]), np.float32) + 0.001)
    micro.demarrer()
    for i in range(0, len(audio) - BLOCK + 1, BLOCK):
        micro.recevoir(audio[i:i + BLOCK])
        time.sleep(BLOCK / RATE)
    micro.arreter()
    fini.wait()
    nettoyeur.arreter()


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--test":
        tester(sys.argv[2])
        return
    # Un seul Djåse : un deuxième lancement ouvre la fenêtre du premier, puis s'arrête.
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = k32.CreateEventW.restype = k32.OpenEventW.restype = ctypes.c_void_p
    mutex = k32.CreateMutexW(None, False, "Local\\Djase")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        evenement = k32.OpenEventW(0x0002, False, "Local\\Djase_ouvrir")  # EVENT_MODIFY_STATE
        if evenement:
            k32.SetEvent(ctypes.c_void_p(evenement))
        journal("Djåse tourne déjà : sa fenêtre s'ouvre.")
        return
    ouvrir = k32.CreateEventW(None, False, False, "Local\\Djase_ouvrir")

    # Identité Windows propre : sinon pythonw.exe est rangé sous Python, avec l'icône de Python.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Djase.Dictee")
    # données du dossier du code déplacées vers %LOCALAPPDATA%\Djåse (version grand public, 05/11/2026) : seulement ici,
    # après le verrou « un seul Djåse », jamais en mode test ; le journal est écrit APRÈS (journal.txt déplacé d'abord)
    faits = preparer()
    for fait in faits:
        journal(f"Données : {fait}")
    # premier lancement d'une version installée : modèles téléchargés (fenêtre seulement s'il en manque)
    import telechargement
    if not telechargement.verifier_ou_telecharger(journal):
        journal("Djåse s'arrête : modèles pas installés (relancez-le pour reprendre le téléchargement).")
        return
    journal(f"Djåse : chargement des modèles ({NOM_MOTEUR} et Qwen)...")
    nettoyeur = Nettoyeur()
    nettoyeur.demarrer()
    pastille_ref = []
    transcripteur = Transcripteur(nettoyeur, lambda t, s: fin_dictee(pastille_ref[0])(t, s))
    micro = Micro(transcripteur)
    pastille = Pastille(micro)
    pastille_ref.append(pastille)
    ETAT["pastille"] = pastille
    try:
        micro.ouvrir()
    except Exception as e:
        journal(f"Micro introuvable ({e}) : branche-le puis relance Djåse.")
        pastille.message("Micro introuvable", alerte=True, duree=8)

    def pret():
        # « Prêt » en dernier, quand TOUT est chargé (demande de Manu du 23/10) : Parakeet, Canary,
        # Grammalecte, Qwen. L'anneau de l'icône passe à l'or au même moment (ETAT["tout_pret"]).
        transcripteur.pret.wait()
        if transcripteur.erreur:  # pas de « Prêt » mensonger : message d'alerte, l'icône reste en chargement
            pastille.message("Moteur de dictée en panne · voir le Journal", alerte=True, duree=20)
            return
        transcripteur.canary_fini.wait()
        transcripteur.grammaire.fini.wait()
        while REGLAGES["nettoyage"] and nettoyeur.proc and nettoyeur.proc.poll() is None \
                and not nettoyeur.pret.wait(0.5):
            pass  # Qwen en chargement (on n'attend plus s'il s'est arrêté)
        ETAT["tout_pret"] = True
        if pastille.etat == "chargement":
            pastille.montrer("cachee")
        journal("Prêt : Ctrl+Win maintenu pour dicter, appui bref pour les mains libres, "
                "Ctrl+Win puis Maj pour recoller la dernière dictée, Ctrl+Win+Alt pour une commande "
                "(retire, retire tout, corrige X par Y).")
    threading.Thread(target=pret, daemon=True).start()
    purger_historique()  # avant charger_derniere et avant le raccourci (rien ne s'écrit en même temps)
    charger_derniere()
    threading.Thread(target=purger_audio, daemon=True).start()
    threading.Thread(target=surveiller_collage, daemon=True).start()
    Raccourcis(micro, pastille).installer()
    try:
        import interface
        # Le module lui-même, pas « import dictee » : sinon une 2e copie avec ses propres réglages.
        appli = interface.Appli(sys.modules[__name__], pastille, transcripteur, nettoyeur)
        appli.demarrer()

        def attendre_second_lancement():
            while k32.WaitForSingleObject(ctypes.c_void_p(ouvrir), 0xFFFFFFFF) == 0:
                appli.ouvrir("historique")
        threading.Thread(target=attendre_second_lancement, daemon=True).start()
    except Exception as e:
        journal(f"Icône près de l'horloge indisponible ({e}) : Djåse marche quand même.")
    pastille.boucle()
    nettoyeur.arreter()
    os._exit(0)  # Quitter (menu) : fils du clavier et du micro arrêtés d'un coup


if __name__ == "__main__":
    main()
