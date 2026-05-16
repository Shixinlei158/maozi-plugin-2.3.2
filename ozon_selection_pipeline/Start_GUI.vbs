Set objShell = CreateObject("WScript.Shell")
objShell.CurrentDirectory = "C:\project\maozi-plugin-2.3.2\ozon_selection_pipeline"
objShell.Run "py -3.9 -m ozon_pipeline.cli gui", 1, False