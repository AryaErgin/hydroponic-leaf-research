"""Build a reviewable candidate package excluding every development entry point.

Not a deployment command. Existing production images are untouched.
"""
import argparse
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED

FILES=('__init__.py','app.py','config.py','database.py','passwords.py','service.py',
       'service_errors.py','mail.py','manage.py','static/index.html','static/app.js','static/style.css')


def build_bundle(destination):
    root=Path(__file__).parent
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(destination,'w',compression=ZIP_DEFLATED) as archive:
        for name in FILES:archive.write(root/name,'src/auth_preview/'+name)
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    print(build_bundle(parser.parse_args().output))
