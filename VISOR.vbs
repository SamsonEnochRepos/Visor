Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\samso\OneDrive\Desktop\VISOR\"
WshShell.Run """C:\Users\samso\OneDrive\Desktop\VISOR\venv\Scripts\pythonw.exe"" ""C:\Users\samso\OneDrive\Desktop\VISOR\main.py""", 0, False
