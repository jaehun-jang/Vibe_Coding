@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ============================================
echo   KIS 자동매매 실행파일(.exe) 만들기
echo ============================================
echo.

where python > nul 2>&1
if errorlevel 1 (
    echo [오류] Python 이 설치되어 있지 않거나 PATH 에 없습니다.
    echo python.org 에서 설치할 때 "Add python.exe to PATH" 를 체크하세요.
    goto fail
)

echo [1/3] 필요한 패키지 설치 중...
python -m pip install --upgrade -r requirements.txt pyinstaller
if errorlevel 1 goto fail

echo.
echo [2/3] 실행파일 만드는 중... (1~2분 걸립니다)
python -m PyInstaller --noconfirm --onefile --console --name KIS_AutoTrader main.py
if errorlevel 1 goto fail

echo.
echo [3/3] 설정 파일 복사 중...
copy /Y config.example.yaml dist\config.example.yaml > nul
if exist config.yaml copy /Y config.yaml dist\config.yaml > nul

echo.
echo ============================================
echo   완료! dist 폴더를 확인하세요.
echo   - KIS_AutoTrader.exe  : 더블클릭해서 실행
echo   - config.yaml         : 같은 폴더에 있어야 합니다
echo ============================================
explorer dist
pause
exit /b 0

:fail
echo.
echo 오류가 발생했습니다. 위 메시지를 확인해 주세요.
pause
exit /b 1
