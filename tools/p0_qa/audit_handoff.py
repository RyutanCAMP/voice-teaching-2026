"""Scan handoff Git blobs and nested archives without printing matching values."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]

def git(*args): return subprocess.check_output(['git', *args], cwd=ROOT)

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', default='20c8464')
    ap.add_argument('--credential-file', help='Optional local file; value is compared in memory only')
    ap.add_argument('--output', default='output/handoff/security.json')
    a = ap.parse_args()
    known = None
    if a.credential_file:
        # Do not echo, hash, store or include the key/path in the report.
        for line in Path(a.credential_file).read_text(encoding='utf-8-sig').splitlines():
            if line.strip().startswith('ANTHROPIC_API_KEY='):
                value = line.split('=',1)[1].strip().strip('"\'')
                if value: known = value.encode()
    findings = []
    patterns = {
        'provider_key': rb'sk-ant-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}',
        'github_credential': rb'gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{25,}',
        'cloud_access_key': rb'AKIA[A-Z0-9]{16}',
        'private_key': rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    }
    scanned = 0
    def scan(name, data, depth=0):
        global scanned
        scanned += 1
        if known and known in data: findings.append(dict(file=name, kind='known_local_credential'))
        for kind, pat in patterns.items():
            for match in re.finditer(pat, data):
                token = match.group()
                if b'TESTKEY' in token or b'DO-NOT-SHOW' in token or b'EXAMPLE' in token: continue
                findings.append(dict(file=name, kind=kind))
        for email in re.findall(rb'[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', data):
            domain = email.split(b'@')[-1].lower()
            if domain.endswith((b'.invalid', b'.test', b'example.com', b'example.org')): continue
            findings.append(dict(file=name, kind='personal_contact_candidate'))
        if depth < 3 and data.startswith(b'\x1f\x8b'):
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
                for member in tar:
                    if member.isfile(): scan(name + '::' + member.name, tar.extractfile(member).read(), depth + 1)
    commits = git('rev-list', a.base + '..HEAD').decode().splitlines()
    seen = set()
    for commit in commits:
        names = git('diff-tree','--no-commit-id','--name-only','-r',commit).decode().splitlines()
        for name in names:
            obj = subprocess.run(['git','rev-parse',commit + ':' + name],cwd=ROOT,capture_output=True)
            if obj.returncode: continue
            blob = obj.stdout.decode().strip()
            if blob in seen: continue
            seen.add(blob)
            scan(commit[:7] + ':' + name, git('cat-file','blob',blob))
    secret_files = [n for n in git('ls-files').decode().splitlines()
                    if Path(n).name in ('.env',) or n.endswith(('.pem','.key','.pfx','.p12'))]
    result = dict(scope='All unique changed Git blobs since base, including prior pushed commits and nested evidence archives',
        base=a.base, tip=git('rev-parse','HEAD').decode().strip(), commits=len(commits), blobs=len(seen),
        payloads_scanned=scanned, known_credential_compared=known is not None,
        tracked_secret_files=secret_files, findings=findings, api_calls=0,
        limitation='Pattern/contact scan and exact local-key comparison; not a proof against every form of personal information.')
    target = ROOT / a.output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    known = None
    print(json.dumps(dict(commits=len(commits), blobs=len(seen), payloads=scanned,
                         findings=len(findings), tracked_secret_files=len(secret_files), api_calls=0)))
    raise SystemExit(1 if findings or secret_files else 0)
