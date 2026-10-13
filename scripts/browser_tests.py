"""Cross-platform test orchestration. Credentials stay in the child environment."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys

root=Path(__file__).resolve().parents[1]
npm=shutil.which('npm.cmd' if os.name=='nt' else 'npm')
if not npm:raise SystemExit('npm not found')
subprocess.run([sys.executable,str(root/'scripts/e2e_account.py'),'create'],check=True)
try:
    account=json.loads((root/'.runtime/e2e-account.json').read_text())
    env={**os.environ,'E2E_EMAIL':account['email'],'E2E_PASSWORD':account['password']}
    result=subprocess.run([npm,'test'],cwd=root/'frontend',env=env)
finally:
    subprocess.run([sys.executable,str(root/'scripts/e2e_account.py'),'remove'],check=True)
sys.exit(result.returncode)
