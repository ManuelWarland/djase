; Installateur de Djåse (Inno Setup 6, version grand public, décision du 05/11/2026).
; Fabrication : "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" djase.iss   (après PyInstaller : dist\Djase\)
; Installation pour l'utilisateur seul, sans droits administrateur. Aucune donnée personnelle dans l'installateur :
; les données vivent dans %LOCALAPPDATA%\Djåse, créées au premier lancement ; les modèles y sont téléchargés.

#define Nom "Djåse"
; dossier de l'exe fabriqué par PyInstaller (autre dossier possible : ISCC /DSource=dist_neuf\Djase djase.iss)
#ifndef Source
  #define Source "dist\Djase"
#endif
#define Version "0.2.0"

[Setup]
AppId={{6B0E2D4C-5A1F-4E7B-9C3D-D7A5E0B1F2C8}
AppName={#Nom}
AppVersion={#Version}
AppVerName={#Nom} {#Version}
AppPublisher=Manuel Warland
VersionInfoDescription={#Nom}, dictée vocale 100 % locale
DefaultDirName={localappdata}\Programs\{#Nom}
DefaultGroupName={#Nom}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist_installateur
OutputBaseFilename=Djase-{#Version}-installation
SetupIconFile=djase_nuit.ico
UninstallDisplayIcon={app}\Djase.exe
UninstallDisplayName={#Nom}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
; la GPL 3 montrée pendant l'installation (texte officiel en anglais : seule version qui fait foi)
LicenseFile=LICENSE
RestartApplications=no

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "demarrage"; Description: "Lancer {#Nom} au démarrage de Windows"; GroupDescription: "Options :"

[Files]
Source: "{#Source}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; licence et mentions à côté de Djase.exe, là où « À propos » les annonce
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "MENTIONS.md"; DestDir: "{app}"; Flags: ignoreversion
; mode d'emploi (fabriqué par guide\build.py), aussi dans le menu Démarrer
Source: "guide\sortie\Djase_Mode_emploi.pdf"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; raccourcis de la 1re installation (06/11/2026), posés en vrac dans le menu Démarrer : remplacés par le dossier Djåse
Type: files; Name: "{userprograms}\{#Nom}.lnk"
Type: files; Name: "{userprograms}\Mode d'emploi de {#Nom}.lnk"
Type: files; Name: "{userprograms}\Désinstaller {#Nom}.lnk"

[Icons]
; un dossier « Djåse » dans le menu Démarrer, avec ses trois raccourcis (choix de Manu du 06/11/2026)
Name: "{group}\{#Nom}"; Filename: "{app}\Djase.exe"; WorkingDir: "{app}"
Name: "{group}\Mode d'emploi de {#Nom}"; Filename: "{app}\Djase_Mode_emploi.pdf"
Name: "{group}\Désinstaller {#Nom}"; Filename: "{uninstallexe}"
; en mise à jour silencieuse, Inno rejoue les tâches de la dernière installation (UsePreviousTasks) : le raccourci de
; démarrage n'est alors recréé que s'il existe encore (Djåse le pose et le retire lui-même depuis son menu)
Name: "{userstartup}\{#Nom}"; Filename: "{app}\Djase.exe"; WorkingDir: "{app}"; Tasks: demarrage; Check: DemarrageAGarder

[Run]
Filename: "{app}\Djase.exe"; Description: "Lancer {#Nom} maintenant (premier lancement : téléchargement des modèles, environ 4,3 Go)"; Flags: nowait postinstall skipifsilent
; mise à jour lancée par Djåse lui-même (installation invisible, /VERYSILENT) : Djåse s'est fermé, on le relance ; en
; installation normale, c'est la case ci-dessus qui s'en charge (skipifsilent), jamais les deux. « Silently » de la
; documentation Inno couvre /SILENT et /VERYSILENT.
Filename: "{app}\Djase.exe"; Flags: nowait skipifnotsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM Djase.exe /T /F"; Flags: runhidden; RunOnceId: "ArretDjase"

[Code]
// Installation normale : la case « Lancer au démarrage de Windows » décide. Mise à jour silencieuse : on ne recrée le
// raccourci que s'il existe encore, pour ne pas le remettre chez quelqu'un qui l'a retiré depuis Djåse.
function DemarrageAGarder: Boolean;
begin
  Result := (not WizardSilent) or FileExists(ExpandConstant('{userstartup}\{#Nom}.lnk'));
end;

// Djåse vit dans la zone de notification, sans fenêtre : le Gestionnaire de redémarrage de Windows ne sait pas le
// fermer (vécu le 06/11/2026 : « L'assistant d'installation n'a pas pu arrêter toutes les applications »). On l'arrête
// nous-mêmes avant la copie ; /T ferme aussi son moteur Qwen (llama-server, processus enfant), sans toucher au llama-server d'un autre programme.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM Djase.exe /T /F', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(800);
  Result := '';
end;

[Messages]
fr.FinishedLabel=L'installation de [name] est terminée.%n%nVos réglages, votre historique et votre dictionnaire seront rangés dans %LOCALAPPDATA%\Djåse. Ils sont gardés si vous désinstallez [name].
