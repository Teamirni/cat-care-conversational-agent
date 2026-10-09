' CatCareAgent 一键启动脚本
' 双击运行：启动本地对话服务并保持窗口，随后自动打开浏览器（带 debug 面板）
' 停止：在弹出的黑色窗口按 Ctrl+C 关闭

Option Explicit
On Error Resume Next

Dim WshShell, PyExe, ServerPy, Port, fso, q
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
q = Chr(34)

If WshShell Is Nothing Then
    MsgBox "无法创建 WScript.Shell 对象，请确认系统组件完好。", 16, "启动失败"
    WScript.Quit 1
End If

PyExe    = "C:\Users\86147\.workbuddy\binaries\python\versions\3.13.12\python.exe"
ServerPy = "D:\工作文件\猫咪健康Agent_复原Demo_2026-08-24\server.py"
Port     = "8787"

If Not fso.FileExists(PyExe) Then
    MsgBox "未找到 Python 解释器：" & vbCrLf & PyExe & vbCrLf & "请确认 WorkBuddy 托管运行环境。", 16, "启动失败"
    WScript.Quit 1
End If
If Not fso.FileExists(ServerPy) Then
    MsgBox "未找到 server.py：" & vbCrLf & ServerPy, 16, "启动失败"
    WScript.Quit 1
End If

' 启动服务（窗口样式 1 = 正常窗口；Ctrl+C 可停止）
WshShell.Run q & PyExe & q & " " & q & ServerPy & q, 1, False
If Err.Number <> 0 Then
    MsgBox "启动命令失败：" & Err.Description, 16, "启动失败"
    WScript.Quit 1
End If

' 等待服务就绪后打开浏览器（默认端口 8787，带 debug 面板）
WScript.Sleep 2500
WshShell.Run "http://127.0.0.1:" & Port & "/?debug= 1", 1, 0
WScript.Echo "CatCareAgent 已启动。" & vbCrLf & _
             "浏览器地址：http://127.0.0.1:" & Port & "/?debug=1" & vbCrLf & _
             "停止方式：在黑色窗口按 Ctrl+C。"
