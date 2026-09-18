Option Explicit
' ===================================================================
'  通常起動の入口(基盤仕様書 2.1)
'
'  利用者が実行するのは**このファイル1つ**です。コンソールを出さずに
'  Python を起動し、あとは待機画面(loading.html)が引き受けます。
'
'  起動しないときは start.bat(診断用)を使ってください。どちらも最後は
'  同じ start_app.py を呼びます ── 通常起動と原因調査用起動を分けるのが
'  目的で、別の道を作るのが目的ではありません。
'
'  このファイルは日本語を含むので **ANSI(Shift_JIS)** で保存します。
'  UTF-8 で保存すると wscript が読めません。
' ===================================================================
Dim shell, fso, here, script, candidates, i, launched

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)
script = here & "\start_app.py"

If Not fso.FileExists(script) Then
    MsgBox "start_app.py が見つかりません。" & vbCrLf & vbCrLf & script & vbCrLf & vbCrLf & _
           "フォルダごとコピーできているか確かめてください。", _
           vbCritical, "起動できません"
    WScript.Quit 1
End If

shell.CurrentDirectory = here

' pythonw を先に試します(コンソールを出さないため)。
' 見つからない端末のために py ランチャーと python も控えにしています。
candidates = Array("pythonw.exe", "pyw.exe -3", "python.exe", "py.exe -3")
launched = False

For i = 0 To UBound(candidates)
    If Not launched Then
        On Error Resume Next
        shell.Run candidates(i) & " """ & script & """", 0, False
        If Err.Number = 0 Then launched = True
        Err.Clear
        On Error GoTo 0
    End If
Next

If Not launched Then
    MsgBox "Python が見つかりませんでした。" & vbCrLf & vbCrLf & _
           "Python 3.11 以上を入れるか、PATH に通してください。" & vbCrLf & _
           "入っているはずなのに出る場合は start.bat を実行すると、" & vbCrLf & _
           "コンソールに理由が出ます。", _
           vbCritical, "起動できません"
    WScript.Quit 1
End If

' **ここでは起動を待ちません。** 待機画面が起動確認APIの応答を見て
' 切り替えるので、この入口の仕事は「呼ぶこと」までです。
