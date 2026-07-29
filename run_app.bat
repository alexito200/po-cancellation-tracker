@echo off
REM Edit the line below to match wherever you extracted poppler --
REM specifically the folder that directly contains pdftotext.exe.
set POPPLER_PATH=C:\Users\AlexA\OneDrive - Haddad Brands\Documents\Poppler\poppler-26.02.0\Library\bin

cd /d "%~dp0"
python -m streamlit run app.py
pause
