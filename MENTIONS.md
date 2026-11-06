# Djåse : licence et mentions

Djåse, dictée vocale 100 % locale. Copyright © 2026 Manuel Warland.

Djåse est un logiciel libre : vous pouvez le redistribuer et le modifier selon les termes de la licence publique générale GNU (GNU GPL), version 3, publiée par la Free Software Foundation. Il est fourni sans aucune garantie. Le texte complet de la licence se trouve dans le fichier `LICENSE`.

Djåse s'appuie sur le travail d'autres personnes. Merci à elles. Voici ce qu'il utilise, avec la licence de chacun.

## Modèles (téléchargés au premier lancement)

| Modèle | Rôle dans Djåse | Auteur | Licence | Source |
|---|---|---|---|---|
| Parakeet TDT 0.6B v3 | reconnaissance de la voix | NVIDIA | CC-BY 4.0 | version ONNX par istupakov : huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx |
| Canary 1B v2 | reconnaissance des dictées longues et des commandes | NVIDIA | CC-BY 4.0 | version ONNX par istupakov : huggingface.co/istupakov/canary-1b-v2-onnx |
| FastConformer français (transducteur, large) | troisième avis sur les dictées courtes | NVIDIA | CC-BY 4.0 | version ONNX par OpenVoiceOS : huggingface.co/OpenVoiceOS/nvidia-fr-conformer-transducer-large-onnx |
| Qwen3 4B Instruct 2507 | mise en forme du texte | équipe Qwen (Alibaba Cloud) | Apache 2.0 | version GGUF par Unsloth : huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF |

Les modèles NVIDIA sont utilisés tels quels, convertis au format ONNX par les personnes citées. Licence CC-BY 4.0 : creativecommons.org/licenses/by/4.0

## Programmes et bibliothèques livrés avec Djåse

| Composant | Licence |
|---|---|
| Grammalecte (correcteur de grammaire) | GPL 3 |
| llama.cpp et ggml (moteur du modèle de langue) | MIT |
| LLVM OpenMP (livré avec llama.cpp) | Apache 2.0 avec exception LLVM |
| ONNX Runtime | MIT |
| onnx-asr | MIT |
| NumPy | BSD-3-Clause (et licences de ses parties : 0BSD, MIT, Zlib, CC0) |
| PortAudio, via sounddevice (micro) | MIT |
| libsndfile, via soundfile (fichiers son) | LGPL 2.1 (bibliothèque fournie à part, remplaçable) |
| soundfile | BSD |
| keyboard (raccourci clavier) | MIT |
| pystray (icône près de l'horloge) | LGPL 3 |
| Pillow (images) | MIT-CMU |
| sv-ttk (thème des fenêtres) | MIT |
| pyperclip (presse-papiers) | BSD |
| huggingface_hub, hf-xet, tokenizers | Apache 2.0 |
| PyYAML, cffi, filelock, anyio, h11, truststore, six | MIT |
| click, httpx2, httpcore2, idna, fsspec, pycparser, protobuf, colorama | BSD |
| tqdm | MPL 2.0 et MIT |
| packaging | Apache 2.0 ou BSD-2-Clause |
| flatbuffers | Apache 2.0 |
| typing_extensions | PSF 2.0 |
| Python | PSF |
| PyInstaller (fabrication de l'exe) | GPL 2 avec exception pour les programmes fabriqués |

Les textes complets de ces licences se trouvent dans les dossiers des bibliothèques, livrés avec Djåse (`_internal`), ou sur leurs sites.
