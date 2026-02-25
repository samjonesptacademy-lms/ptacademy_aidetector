@echo off
echo =========================================
echo  PT Academy AI Content Checker
echo =========================================
echo.
echo Installing dependencies...
pip install flask --quiet
echo.
echo Starting application...
echo Open your browser at: http://localhost:5000
echo Press Ctrl+C to stop the server.
echo.
python app.py
pause
