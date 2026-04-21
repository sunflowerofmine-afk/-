@echo off
cd /d "C:\Users\purpl\Jongbe Project\korea-close-betting-bot"
"C:\Users\purpl\AppData\Local\Programs\Python\Python313\python.exe" -m scripts.pipeline >> logs\scheduler.log 2>&1
git add reports/ index.html >> logs\scheduler.log 2>&1
git diff --cached --quiet && echo "No changes to commit" >> logs\scheduler.log 2>&1 || git commit -m "chore: update dashboard reports [skip ci]" >> logs\scheduler.log 2>&1
git pull --rebase --autostash origin main >> logs\scheduler.log 2>&1
git push >> logs\scheduler.log 2>&1
