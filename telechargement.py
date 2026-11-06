"""Premier lancement : télécharge les modèles qui manquent (version grand public, décision du 05/11/2026).

Les modèles (environ 4,3 Go) ne sont pas dans l'installateur. Au lancement, s'il en manque, une petite fenêtre aux
couleurs de la charte montre la progression ; sinon rien ne s'affiche. Chaque fichier :
- est d'abord cherché dans le cache Hugging Face du PC (copié, pas retéléchargé) ;
- sinon téléchargé depuis Hugging Face sous un nom provisoire `.partiel`, repris là où il s'était arrêté ;
- contrôlé (taille, et empreinte SHA-256 pour les gros fichiers) avant de prendre son vrai nom.
Sources et licences : Qwen3 4B (unsloth, Apache 2.0), Parakeet et Canary (NVIDIA via istupakov, CC-BY 4.0), modèle
français NVIDIA (OpenVoiceOS, CC-BY 4.0) ; mentionnés dans « À propos ».
"""
import hashlib
import http.client
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from chemins import MODELES

HF = "https://huggingface.co/{depot}/resolve/{revision}/{fichier}"

# Révision exacte de chaque dépôt (relevée le 06/11/2026) : une mise à jour faite par l'auteur d'un dépôt ne change
# jamais ce que Djåse télécharge (tailles et empreintes ci-dessous restent justes, relecture du 05/11)
REVISIONS = {
    "unsloth/Qwen3-4B-Instruct-2507-GGUF": "a06e946bb6b655725eafa393f4a9745d460374c9",
    "istupakov/parakeet-tdt-0.6b-v3-onnx": "8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce",
    "istupakov/canary-1b-v2-onnx": "5ebc1520cef7b6b318b3526ad17adbfe00bc1bfc",
    "OpenVoiceOS/nvidia-fr-conformer-transducer-large-onnx": "19032aee39bfb341e67b191dfd80c902e7271436",
}

# (dossier dans MODELES, dépôt Hugging Face, [(fichier, taille, sha256 ou "")])
MODELES_REQUIS = [
    ("", "unsloth/Qwen3-4B-Instruct-2507-GGUF", [
        ("Qwen3-4B-Instruct-2507-Q4_K_M.gguf", 2497281120,
         "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597")]),
    ("parakeet-tdt-0.6b-v3", "istupakov/parakeet-tdt-0.6b-v3-onnx", [
        ("config.json", 97, ""),
        ("vocab.txt", 93939, ""),
        ("decoder_joint-model.int8.onnx", 18202004, "eea7483ee3d1a30375daedc8ed83e3960c91b098812127a0d99d1c8977667a70"),
        ("encoder-model.int8.onnx", 652183999, "6139d2fa7e1b086097b277c7149725edbab89cc7c7ae64b23c741be4055aff09")]),
    ("canary-1b-v2", "istupakov/canary-1b-v2-onnx", [
        ("config.json", 68, ""),
        ("vocab.txt", 208022, ""),
        ("decoder-model.int8.onnx", 170040374, "52d83aa7aad41fbbe4f9dfcd341d784735a6eb4c6eb0d3290fc27a0d8ac39abf"),
        ("encoder-model.int8.onnx", 859078138, "6d96e9945898e5ace48f4efecd459ca1df81859730be27b8af6b197639403ee1")]),
    ("nvidia-fr-conformer-transducer-large-onnx", "OpenVoiceOS/nvidia-fr-conformer-transducer-large-onnx", [
        ("config.json", 120, ""),
        ("vocab.txt", 928, ""),
        ("decoder_joint-model.int8.onnx", 4214216, "791d3532373973021130b2afefd08d62c461db1a7b7f35a1ef5ca090766ad256"),
        ("encoder-model.int8.onnx", 137420853, "016ccef427c257ce54dab28372e98be45aa1b5da7bf8366902b0302b4c6a6cfc")]),
]


def _cible(dossier, fichier):
    return MODELES / dossier / fichier if dossier else MODELES / fichier


def manquants():
    """[(chemin, dépôt, fichier, taille, sha256)] des fichiers absents. Un fichier présent n'est pas re-contrôlé (le
    contrôle complet d'un modèle de 2,5 Go prendrait une minute à chaque lancement)."""
    liste = []
    for dossier, depot, fichiers in MODELES_REQUIS:
        for fichier, taille, sha in fichiers:
            chemin = _cible(dossier, fichier)
            if not chemin.exists():
                liste.append((chemin, depot, fichier, taille, sha))
    return liste


def _depuis_cache(depot, fichier):
    """Le fichier dans le cache Hugging Face du PC, s'il y est (Parakeet chargé par onnx_asr avant cette version)."""
    try:
        from huggingface_hub import try_to_load_from_cache
        p = try_to_load_from_cache(depot, fichier, revision=REVISIONS[depot])
        return Path(p) if isinstance(p, str) and Path(p).is_file() else None
    except Exception:
        return None


def _controle(chemin, taille, sha, avance=None):
    if chemin.stat().st_size != taille:
        return False
    if not sha:
        return True
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
            if avance:
                avance(len(bloc))
    return h.hexdigest() == sha


class Telechargement:
    """Télécharge `manquants()` dans un fil ; l'interface lit `fait`, `total`, `etape`, `fini`, `erreur`."""

    def __init__(self, liste):
        self.liste = liste
        self.total = sum(t for _, _, _, t, _ in liste)
        self.fait = 0
        self.termine = 0  # octets des fichiers déjà finis (la progression = termine + ce que contient le .partiel)
        self._h, self._h_octets = hashlib.sha256(), 0  # empreinte du fichier en cours, au fil de l'écriture
        self.etape = ""
        self.fini = False
        self.erreur = None
        self.annule = False

    def demarrer(self):
        threading.Thread(target=self._tout, daemon=True, name="telechargement-modeles").start()

    def _tout(self):
        # priorité « arrière-plan » (disque et processeur) pour ce fil : sur un disque mécanique, plusieurs Go écrits
        # ralentissaient tout le PC (Manu, 06/11/2026) ; Windows fait passer avant ce que la personne fait en même temps
        # Types déclarés : sans eux, la « pseudo-poignée » du fil (-1/-2) était tronquée en 32 bits et l'appel échouait
        # sans un mot (mesuré par la relecture du 06/11/2026 : erreur 6, poignée invalide).
        self.priorite_basse = False
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentThread.restype = wintypes.HANDLE
            k32.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
            k32.SetThreadPriority.restype = wintypes.BOOL
            self.priorite_basse = bool(k32.SetThreadPriority(k32.GetCurrentThread(), 0x00010000))  # MODE_BACKGROUND_BEGIN
        except Exception:
            pass
        try:
            libre = shutil.disk_usage(MODELES.resolve().anchor).free
            if libre < self.total + 500_000_000:
                raise RuntimeError(f"pas assez de place sur le disque ({libre / 1e9:.1f} Go libres, "
                                   f"{self.total / 1e9:.1f} Go nécessaires)")
            for chemin, depot, fichier, taille, sha in self.liste:
                if self.annule:
                    return
                self._un(chemin, depot, fichier, taille, sha)
            self.fini = True
        except Exception as e:
            self.erreur = str(e)

    def _un(self, chemin, depot, fichier, taille, sha, sans_cache=False):
        chemin.parent.mkdir(parents=True, exist_ok=True)
        partiel = chemin.with_name(chemin.name + ".partiel")
        cache = None if sans_cache else _depuis_cache(depot, fichier)
        copie = False
        # empreinte calculée au fil des blocs écrits : plus de relecture complète du fichier pour le contrôle (2,5 Go
        # relus pour Qwen sur le disque mécanique de Manu, 06/11/2026)
        self._h, self._h_octets = hashlib.sha256(), 0
        if cache and cache.stat().st_size == taille:
            self.etape = f"Copie de {fichier} (déjà sur ce PC)"
            copie = True
            with open(cache, "rb") as src, open(partiel, "wb") as dst:
                for bloc in iter(lambda: src.read(1 << 20), b""):
                    dst.write(bloc)
                    self._h.update(bloc)
                    self._h_octets += len(bloc)
        else:
            # Coupure du serveur en cours de route (vécu chez Manu le 06/11/2026 à 01h11 : Qwen arrêté avant la fin
            # sans erreur, jugé abîmé, 2,4 Go supprimés) : on reprend là où on en était, plusieurs fois ; un fichier
            # simplement incomplet n'est JAMAIS supprimé.
            essais = 0
            while True:
                deja = partiel.stat().st_size if partiel.exists() else 0
                if deja > taille:
                    partiel.unlink()
                    deja = 0
                    self._h, self._h_octets = hashlib.sha256(), 0  # sinon relecture d'un fichier qui n'existe plus
                self.fait = self.termine + deja
                if sha and self._h_octets != deja:
                    # reprise d'un .partiel (lancement précédent, ou coupure) : son début est lu une fois pour l'empreinte
                    self._h, self._h_octets = hashlib.sha256(), 0
                    with open(partiel, "rb") as f:
                        for bloc in iter(lambda: f.read(1 << 20), b""):
                            self._h.update(bloc)
                            self._h_octets += len(bloc)
                if deja == taille:
                    break  # complet (y compris annulé pendant le contrôle) : au contrôle, sans requête (pas de 416)
                if essais >= 8:
                    raise RuntimeError(f"connexion coupée {essais} fois pendant {fichier} ; rien n'est perdu, "
                                       f"relancez Djåse pour reprendre")
                essais += 1
                self.etape = f"Téléchargement de {fichier}" + (f" (reprise n° {essais - 1})" if essais > 1 else "")
                try:
                    self._recevoir(partiel, depot, fichier, deja)
                except urllib.error.HTTPError as e:
                    if e.code not in (408, 429, 500, 502, 503, 504):
                        raise
                    time.sleep(min(30, 3 * essais))
                except (OSError, http.client.HTTPException) as e:  # connexion coupée, délai dépassé, réception tronquée
                    if getattr(e, "errno", None) == 28 or getattr(e, "winerror", None) in (39, 112):
                        # disque plein : inutile de réessayer (et de relire 2,5 Go à chaque essai)
                        raise RuntimeError(f"disque plein pendant {fichier} ; rien n'est perdu : libérez de la "
                                           f"place, puis relancez Djåse pour reprendre") from e
                    time.sleep(min(30, 3 * essais))
                if self.annule:
                    return  # le .partiel est gardé tel quel : reprise au lancement suivant
        if sha:
            self.etape = f"Contrôle de {fichier}"
        if sha and self._h_octets == taille == partiel.stat().st_size:
            bon = self._h.hexdigest() == sha  # empreinte déjà calculée pendant l'écriture
        else:
            bon = _controle(partiel, taille, sha)
        if not bon:
            # complet mais faux (empreinte différente) : là seulement, le fichier est supprimé
            partiel.unlink(missing_ok=True)
            if copie:
                # copie du cache Hugging Face abîmée : on télécharge plutôt que de recopier le même fichier à chaque
                # lancement (l'installation n'aboutirait jamais, relecture du 06/11/2026)
                return self._un(chemin, depot, fichier, taille, sha, sans_cache=True)
            raise RuntimeError(f"{fichier} abîmé pendant le transfert (supprimé) : relancez Djåse pour réessayer")
        partiel.replace(chemin)
        self.termine += taille
        self.fait = self.termine

    def _recevoir(self, partiel, depot, fichier, deja):
        url = HF.format(depot=depot, revision=REVISIONS[depot], fichier=fichier)
        requete = urllib.request.Request(url, headers={"User-Agent": "Djase", **({"Range": f"bytes={deja}-"} if deja else {})})
        try:
            r = urllib.request.urlopen(requete, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code != 416:
                raise
            partiel.unlink(missing_ok=True)  # reprise refusée : on repart de zéro
            deja = 0
            self._h, self._h_octets = hashlib.sha256(), 0
            r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Djase"}), timeout=60)
        with r, open(partiel, "ab" if deja else "wb") as f:
            if deja and r.status != 206:  # le serveur ne reprend pas : on repart de zéro
                f.seek(0)
                f.truncate()
                self._h, self._h_octets = hashlib.sha256(), 0
            while not self.annule:
                bloc = r.read(1 << 20)
                if not bloc:
                    break
                f.write(bloc)
                self._h.update(bloc)
                self._h_octets += len(bloc)
                self.fait = self.termine + f.tell()


# ---- Fenêtre du premier lancement (charte LumAutoSam) --------------------------------------------------------------

FOND, ENCART, BANDEAU, VERT, ROUGE, BOUTON, TEXTE = "#1a1a2e", "#16213e", "#1a2744", "#2ecc71", "#e74c3c", "#2980b9", "#e6e6e6"


def verifier_ou_telecharger(journal):
    """Rien à faire : rend True aussitôt. Sinon fenêtre de téléchargement ; True une fois tout en place, False si la
    personne annule ou si le téléchargement échoue (Djåse s'arrête alors proprement)."""
    liste = manquants()
    if not liste:
        return True
    import tkinter as tk
    from tkinter import ttk

    t = Telechargement(liste)
    journal(f"Premier lancement : {len(liste)} fichier(s) de modèles à installer ({t.total / 1e9:.1f} Go) "
            f"dans {MODELES}.")
    racine = tk.Tk()
    racine.title("Djåse : premier lancement")
    racine.configure(bg=FOND)
    racine.resizable(False, False)
    try:
        from chemins import PROGRAMME
        racine.iconbitmap(str(PROGRAMME / "djase_nuit.ico"))
    except Exception:
        pass
    tk.Label(racine, text="Djåse : premier lancement", bg=BANDEAU, fg="white", font=("Segoe UI", 12, "bold"),
             anchor="w", padx=16, pady=10).pack(fill="x")
    corps = tk.Frame(racine, bg=ENCART, padx=18, pady=14)
    corps.pack(fill="both", expand=True, padx=12, pady=12)
    tk.Label(corps, text=f"Djåse installe ses modèles de reconnaissance vocale et de correction\n"
                         f"({t.total / 1e9:.1f} Go, une seule fois). Tout reste ensuite sur ce PC.",
             bg=ENCART, fg=TEXTE, font=("Segoe UI", 10), justify="left").pack(anchor="w")
    style = ttk.Style(racine)
    style.theme_use("clam")
    style.configure("Djase.Horizontal.TProgressbar", troughcolor=FOND, background=VERT, bordercolor=ENCART,
                    lightcolor=VERT, darkcolor=VERT)
    barre = ttk.Progressbar(corps, style="Djase.Horizontal.TProgressbar", length=460, maximum=1000)
    barre.pack(pady=(14, 6))
    etat = tk.Label(corps, text="Préparation…", bg=ENCART, fg=TEXTE, font=("Segoe UI", 9), anchor="w")
    etat.pack(fill="x")
    resultat = {"ok": False}

    def annuler():
        t.annule = True
        if not t.erreur:  # après un échec, le bouton dit « Fermer » : rien d'annulé
            journal("Premier lancement : téléchargement annulé.")
        racine.destroy()

    bouton = tk.Button(corps, text="Annuler", command=annuler, bg=BOUTON, fg="white", activebackground="#3498db",
                       relief="flat", padx=14, pady=4, font=("Segoe UI", 9, "bold"))
    bouton.pack(anchor="e", pady=(10, 0))
    racine.protocol("WM_DELETE_WINDOW", annuler)
    debut = time.time()

    def suivre():
        if t.erreur:
            journal(f"Premier lancement : échec ({t.erreur}).")
            etat.configure(text=f"Échec : {t.erreur}", fg=ROUGE)
            bouton.configure(text="Fermer")
            return
        if t.fini:
            journal("Premier lancement : modèles installés.")
            resultat["ok"] = True
            racine.destroy()
            return
        part = t.fait / t.total if t.total else 0
        barre["value"] = int(part * 1000)
        ecoule = time.time() - debut
        reste = f", encore environ {int((ecoule / part - ecoule) / 60) + 1} min" if part > 0.02 else ""
        etat.configure(text=f"{t.etape} : {t.fait / 1e9:.2f} / {t.total / 1e9:.1f} Go ({part:.0%}{reste})")
        racine.after(500, suivre)

    t.demarrer()
    racine.after(300, suivre)
    racine.mainloop()
    return resultat["ok"]
