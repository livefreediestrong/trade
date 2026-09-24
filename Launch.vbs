Option Explicit
Dim shell, files, root, command, result
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
command = "powershell.exe -NoProfile -WindowStyle Hidden -File " & Chr(34) & root & "\Start-Tomahawk.ps1" & Chr(34)
result = shell.Run(command, 0, True)
WScript.Quit result
