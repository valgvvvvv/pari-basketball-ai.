"""Private continuation checkpoint; publish aggregate shadow weights only."""
import io
import json
import os
from pathlib import Path
import sys
import urllib.parse
import urllib.request
import zipfile

from publish_daily_archive import api_json, download_artifact, pages, REPO, HEADERS

ROOT = Path('learning-checkpoint')


def restore():
    ROOT.mkdir(exist_ok=True)
    # Accept only the owner's main-branch learning workflow, not PR artifacts.
    for item in pages('/repos/'+REPO+'/actions/artifacts', 'artifacts'):
        if item.get('name') != 'learning-checkpoint' or item.get('expired'):
            continue
        run = api_json('/repos/'+REPO+'/actions/runs/'+str(item['workflow_run']['id']))
        if run.get('head_branch') != 'main' or run.get('path') != '.github/workflows/learning-model.yml':
            continue
        if item.get('size_in_bytes', 0) > 64*1024*1024:
            raise ValueError('Learning checkpoint exceeds bounded storage')
        with zipfile.ZipFile(io.BytesIO(download_artifact(item['archive_download_url']))) as z:
            for name in ('checkpoint.json.gz', 'learning-model.json'):
                if name in z.namelist():
                    if z.getinfo(name).file_size > 64*1024*1024:
                        raise ValueError('Expanded checkpoint exceeds limit')
                    (ROOT/name).write_bytes(z.read(name))
        if not (ROOT/'checkpoint.json.gz').exists():
            raise ValueError('Existing checkpoint is incomplete; do not reset history')
        print('Learning checkpoint restored')
        return
    print('First learning run: bundled historical seed will be used')


def publish():
    path = ROOT/'learning-model.json'
    if not path.exists():
        raise ValueError('No validated candidate model to publish')
    artifact = json.loads(path.read_text(encoding='utf-8'))
    if artifact.get('calibrated') is not False or artifact.get('automatic_priority_enabled') is not False:
        raise ValueError('Only shadow weights may be published')
    tag = 'learning-models'
    releases = list(pages('/repos/'+REPO+'/releases'))
    release = next((r for r in releases if r['tag_name'] == tag), None)
    if release is None:
        release = api_json('/repos/'+REPO+'/releases', 'POST', dict(tag_name=tag, target_commitish='main',
            name='Experimental learning models', prerelease=True,
            body='Aggregate shadow model weights. Historical replay is not independently verified live performance. No automatic betting-signal promotion.'))
    name = 'learning-model-'+artifact['version']+'.json'
    assets = list(pages('/repos/'+REPO+'/releases/'+str(release['id'])+'/assets'))
    if any(a['name'] == name for a in assets):
        print('Model weights unchanged; no duplicate release asset')
        return
    url = release['upload_url'].split('{')[0]+'?'+urllib.parse.urlencode({'name':name})
    request = urllib.request.Request(url, data=path.read_bytes(), headers={**HEADERS, 'Content-Type':'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status != 201:
            raise RuntimeError('Model publication failed')
    print('Published aggregate shadow model '+artifact['version'])


if __name__ == '__main__':
    if REPO != 'valgvvvvv/pari-basketball-ai.':
        raise ValueError('Unexpected repository')
    {'restore':restore, 'publish':publish}[sys.argv[1]]()
