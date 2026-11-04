"""Local secret setup/check only. Never prints a key and never calls Claude."""
import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from voice_teaching import claude_interpreter as ci

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--configure', action='store_true')
    a = ap.parse_args()
    path = ROOT / '.env'
    ignored = subprocess.run(['git','check-ignore','-q','.env'],cwd=ROOT).returncode == 0
    tracked = subprocess.run(['git','ls-files','--error-unmatch','.env'],cwd=ROOT,
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode == 0
    if not ignored or tracked:
        ap.exit(2, 'Local .env is not safely ignored. No changes.\n')
    if a.configure:
        if path.exists(): ap.exit(2, 'Existing .env preserved. Edit it locally in your trusted editor.\n')
        key = getpass.getpass('Claude API key (hidden; local ignored .env only): ')
        if not ci.key_is_usable(key): ap.exit(2, 'Key format not usable. No file written.\n')
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
                f.write('ANTHROPIC_API_KEY=' + key + '\nANTHROPIC_MODEL=claude-sonnet-5-5\n')
        except OSError:
            ap.exit(2, 'Could not create local .env. No key is displayed.\n')
        key = None
    values = {}
    ci.load_dotenv(path, values)
    key = values.pop('ANTHROPIC_API_KEY', '')
    usable = ci.key_is_usable(key) if key else False
    model = values.get('ANTHROPIC_MODEL', 'claude-sonnet-5-5')
    values.clear()
    print(json.dumps(dict(local_env_exists=path.exists(), git_ignored=ignored, git_tracked=tracked,
        key_present=bool(key), key_format_usable=usable,
        model='claude-sonnet-5-5' if model == 'claude-sonnet-5-5' else 'UNEXPECTED_MODEL',
        endpoint='https://api.anthropic.com/v1/messages', api_calls=0)))
    key = None
    raise SystemExit(0 if usable else 2)
