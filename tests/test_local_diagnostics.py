from contextlib import closing
import json
from pathlib import Path
import sqlite3

from src.local_setup import runtime


def test_snapshot_never_recurses_media_or_unknown_databases(tmp_path,monkeypatch):
    state=tmp_path/'local';runtime.claim_storage(state)
    databases=[state/'auth/auth.sqlite3',state/'science/organizations'/('a'*64)/'hydroponic.sqlite3']
    for path in databases:
        path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE fixture(value TEXT)');db.commit()
    media=databases[1].parent/'media';media.mkdir()
    # Media may have extended Windows paths or vanish during atomic writes.
    def forbidden_scan(*args,**kwargs):raise FileNotFoundError('media is not a database directory')
    monkeypatch.setattr(Path,'rglob',forbidden_scan)
    monkeypatch.setattr(runtime,'auth_request',lambda path:{'users':[]} if 'batchGet' in path else {})
    runtime.snapshot_auth(state)
    result=json.loads((state/'identity-state.json').read_text())
    assert set(result['sqlite_backups'])=={'auth/auth.sqlite3','science/organizations/'+('a'*64)+'/hydroponic.sqlite3'}
