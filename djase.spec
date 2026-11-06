# Recette PyInstaller de Djåse (version grand public, décision du 05/11/2026) : un dossier (onedir), jamais un
# fichier unique (onefile, décompressé dans Temp à chaque lancement). Fabrication :
#   .venv\Scripts\python.exe -m PyInstaller --noconfirm djase.spec
# Résultat : dist\Djase\Djase.exe (+ dist\Djase\_internal). Les modèles n'y sont PAS (téléchargés au premier
# lancement, ou désignés par la variable DJASE_MODELES pour un essai) ; aucune donnée personnelle non plus (elles
# vivent dans %LOCALAPPDATA%\Djåse).
from PyInstaller.utils.hooks import collect_all

datas = [
    ("ui", "ui"),
    ("djase_nuit.ico", "."),
    ("dictionnaire.defaut.json", "."),  # dictionnaire de départ NEUTRE (jamais celui de Manu)
    ("llama", "llama"),                 # llama-server.exe et ses DLL (modèle de langue, Qwen)
]
binaries = []
hiddenimports = ["chemins", "interface", "oreilles"]
# bibliothèques chargées à la demande dans le code (import dans une fonction) : à déclarer avec leurs fichiers
for paquet in ["grammalecte", "onnx_asr", "onnxruntime", "sounddevice", "soundfile", "sv_ttk", "pystray",
               "huggingface_hub", "tokenizers"]:
    d, b, h = collect_all(paquet)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["dictee.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # moteur Whisper : proposé nulle part dans l'interface (reglages.json à la main seulement) ; ~65 Mo évités.
    # S'il est demandé, Djåse le dit dans son journal (Transcripteur._charger).
    excludes=["faster_whisper", "ctranslate2", "av", "torch", "matplotlib", "pytest", "IPython"],
    noarchive=False,
)
# Installation vierge : « direct_url.json » (fiche d'installation d'un paquet) garde le chemin du PC où il a été
# installé, avec le nom du compte Windows (trouvé pour Grammalecte le 06/11/2026) : jamais livré
a.datas = [d for d in a.datas if not d[0].replace("\\", "/").endswith("direct_url.json")]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Djase",
    icon="djase_nuit.ico",
    console=False,          # pas de fenêtre noire
    version="version_djase.txt",
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Djase", upx=False)
