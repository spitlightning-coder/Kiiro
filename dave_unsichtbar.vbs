' Startet Dave ohne Fenster im Hintergrund (fuer den Autostart).
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = dir
sh.Run "cmd /c """ & dir & "\dave_dienst.bat""", 0, False
